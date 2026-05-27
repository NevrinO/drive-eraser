#!/bin/bash
# =============================================================================
# Drive Wipe Station - Install Script
# =============================================================================
# Usage:
#   bash scripts/install.sh
#
# Run as root or with sudo.
# Safe to re-run (idempotent).
# =============================================================================

set -e  # Exit immediately on error

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

APP_NAME="drive-eraser"
APP_USER="wipestation"
INSTALL_DIR="/opt/drive-eraser"
SERVICE_NAME="drive-eraser"
PYTHON_BIN="python3"
VENV_DIR="$INSTALL_DIR/venv"
DATA_DIR="$INSTALL_DIR/data"
LOG_DIR="$DATA_DIR/logs"
ACTIVE_LOG_DIR="$LOG_DIR/active"
FAILED_LOG_DIR="$LOG_DIR/failed"
CERT_DIR="$DATA_DIR/certs"
DB_PATH="$DATA_DIR/wipes.db"
CONFIG_DIR="$INSTALL_DIR/config"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
SMARTCTL_PATH=""
HDPARM_PATH=""
NVME_PATH=""
SG_SANITIZE_PATH=""
SG_INQ_PATH=""
DD_PATH=""
LSBLK_PATH=""
LSHW_PATH=""
SYSTEMCTL_PATH=""

# Default config parameters (overwritten if run interactively)
STATION_ID="wipe-station-01"
WIPE_PORT=5000
WIPE_PASSPHRASE=""
LAN_PASSPHRASE="eraser123"
SLACK_WEBHOOK=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

require_root() {
    if [ "$EUID" -ne 0 ]; then
        error "Please run as root or with sudo."
    fi
}

find_cmd_path() {
    local cmd="$1"
    local path
    path="$(command -v "$cmd" 2>/dev/null || true)"
    if [ -z "$path" ]; then
        error "Required command not found: $cmd"
    fi
    echo "$path"
}

resolve_command_paths() {
    info "Resolving command paths..."

    SMARTCTL_PATH="$(find_cmd_path smartctl)"
    HDPARM_PATH="$(find_cmd_path hdparm)"
    NVME_PATH="$(find_cmd_path nvme)"
    SG_SANITIZE_PATH="$(find_cmd_path sg_sanitize)"
    SG_INQ_PATH="$(find_cmd_path sg_inq)"
    DD_PATH="$(find_cmd_path dd)"
    LSBLK_PATH="$(find_cmd_path lsblk)"
    LSHW_PATH="$(find_cmd_path lshw)"
    SYSTEMCTL_PATH="$(find_cmd_path systemctl)"

    success "Command paths resolved."
}

write_command_paths_config() {
    info "Writing command path config..."

    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_DIR/command_paths.json" << EOF
{
  "smartctl": "$SMARTCTL_PATH",
  "hdparm": "$HDPARM_PATH",
  "nvme": "$NVME_PATH",
  "sg_sanitize": "$SG_SANITIZE_PATH",
  "sg_inq": "$SG_INQ_PATH",
  "dd": "$DD_PATH",
  "lsblk": "$LSBLK_PATH",
  "lshw": "$LSHW_PATH",
  "systemctl": "$SYSTEMCTL_PATH"
}
EOF

    success "Command path config written."
}

# -----------------------------------------------------------------------------
# STEP 1 - Pre-flight checks
# -----------------------------------------------------------------------------

preflight() {
    info "Running pre-flight checks..."

    require_root

    # Check OS
    if ! grep -qi "ubuntu" /etc/os-release; then
        warn "This script is designed for Ubuntu. Proceed with caution."
    fi

    # Check internet
    if ! ping -c 1 -W 3 8.8.8.8 &>/dev/null; then
        warn "No internet connection detected. Package installs may fail."
    fi

    success "Pre-flight checks passed."
}

# -----------------------------------------------------------------------------
# STEP 2 - Install system packages
# -----------------------------------------------------------------------------

install_packages() {
    info "Updating package lists..."
    apt-get update -qq

    info "Installing system dependencies..."
    apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
        git \
        sqlite3 \
        smartmontools \
        nvme-cli \
        sg3-utils \
        hdparm \
        curl \
        util-linux \
        lshw \
        rsync

    success "System packages installed."
}

# -----------------------------------------------------------------------------
# STEP 3 - Create system user
# -----------------------------------------------------------------------------

create_user() {
    info "Setting up application user..."

    if id "$APP_USER" &>/dev/null; then
        warn "User '$APP_USER' already exists. Skipping creation."
    else
        useradd \
            --system \
            --no-create-home \
            --shell /usr/sbin/nologin \
            "$APP_USER"
        success "User '$APP_USER' created."
    fi
}

# -----------------------------------------------------------------------------
# STEP 4 - Copy application files
# -----------------------------------------------------------------------------

install_app() {
    info "Installing application to $INSTALL_DIR..."

    # Create install directory
    mkdir -p "$INSTALL_DIR"

    # Copy app files (preserve existing configs)
    rsync -a \
        --exclude='venv/' \
        --exclude='data/' \
        --exclude='logs/' \
        --exclude='config/bay_map.json' \
        --exclude='config/policy.json' \
        "$REPO_DIR/" "$INSTALL_DIR/"

    success "Application files copied."
}

# -----------------------------------------------------------------------------
# STEP 5 - Set up config files (only if they don't exist)
# -----------------------------------------------------------------------------

prompt_interactive_config() {
    # Only execute interactive prompts if standard input is a terminal TTY
    if [ -t 0 ]; then
        echo ""
        echo -e "${YELLOW}================================================================${NC}"
        echo -e "${YELLOW}   Drive Wipe Station - Interactive Configuration               ${NC}"
        echo -e "${YELLOW}================================================================${NC}"
        echo ""

        # 1. Prompt for Station ID
        echo -e -n "  Enter Station Identifier [Default: wipe-station-01]: "
        local temp_station
        read -r temp_station
        if [ -n "$temp_station" ]; then
            STATION_ID="$temp_station"
            success "Station ID set to: $STATION_ID"
        else
            info "Using default Station ID: $STATION_ID"
        fi
        echo ""

        # 2. Prompt for Port
        echo -e -n "  Enter Bind Port [Default: 5000]: "
        local temp_port
        read -r temp_port
        if [ -n "$temp_port" ]; then
            if [[ "$temp_port" =~ ^[0-9]+$ ]] && [ "$temp_port" -gt 1024 ] && [ "$temp_port" -lt 65536 ]; then
                WIPE_PORT="$temp_port"
                success "Bind Port set to: $WIPE_PORT"
            else
                warn "Invalid port entered. Falling back to default: $WIPE_PORT"
            fi
        else
            info "Using default Port: $WIPE_PORT"
        fi
        echo ""

        # 3. Prompt for LAN Passphrase
        echo -e -n "  Enter LAN Passphrase for Remote UI Gate [Default: eraser123]: "
        local temp_lan_pass
        read -r temp_lan_pass
        if [ -n "$temp_lan_pass" ]; then
            LAN_PASSPHRASE="$temp_lan_pass"
            success "LAN Passphrase successfully staged."
        else
            info "Using default LAN Passphrase: $LAN_PASSPHRASE"
        fi
        echo ""

        # 4. Prompt for Cryptographic Wipe Passphrase (Salt Signer)
        echo -e "  Entering a Cryptographic Passphrase enables secure HMAC-SHA256 signature signing."
        echo -e "  Leave blank to run in Unauthenticated State."
        while true; do
            echo -e -n "  Enter Cryptographic Passphrase [Optional - Press Enter to Skip]: "
            local pass=""
            local confirm=""
            read -r -s pass
            echo ""
            
            if [ -z "$pass" ]; then
                info "Skipping Cryptographic Passphrase setup."
                break
            fi

            echo -e -n "  Confirm Cryptographic Passphrase: "
            read -r -s confirm
            echo ""

            if [ "$pass" = "$confirm" ]; then
                WIPE_PASSPHRASE="$pass"
                success "Cryptographic Passphrase staged."
                break
            else
                warn "Passphrases did not match. Please try again."
            fi
        done
        echo ""

        # 5. Prompt for Slack Webhook URL
        echo -e -n "  Enter Slack Webhook URL for instant alerting [Optional - Press Enter to Skip]: "
        local temp_slack
        read -r temp_slack
        if [ -n "$temp_slack" ]; then
            SLACK_WEBHOOK="$temp_slack"
            success "Slack Webhook staged."
        else
            info "Slack alerting disabled (no URL provided)."
        fi
        echo ""

    else
        info "Non-interactive terminal detected. Skipping prompts and applying defaults."
    fi
}

setup_config() {
    info "Setting up configuration files..."

    mkdir -p "$CONFIG_DIR"

     # bay_map.json - only create if missing
    if [ ! -f "$CONFIG_DIR/bay_map.json" ]; then
        info "Creating default clean-slate bay_map.json..."
        cat > "$CONFIG_DIR/bay_map.json" << 'EOF'
{
  "bay0": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay 0",
    "by_path": null
  },
  "bay1": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay 1",
    "by_path": null
  },
  "bay2": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay 2",
    "by_path": null
  },
  "bay3": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay 3",
    "by_path": null
  },
  "bay4": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay 4",
    "by_path": null
  },
  "bay5": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay 5",
    "by_path": null
  },
  "bay6": {
    "role": "wipe",
    "locked": false,
    "type": "u2",
    "label": "Work Bay 6",
    "by_path": null
  },
  "bay7": {
    "role": "wipe",
    "locked": false,
    "type": "u2",
    "label": "Work Bay 7",
    "by_path": null
  },
  "bay8": {
    "role": "wipe",
    "locked": false,
    "type": "u2",
    "label": "Work Bay 8",
    "by_path": null
  },
  "bay9": {
    "role": "wipe",
    "locked": false,
    "type": "u2",
    "label": "Work Bay 9",
    "by_path": null
  }
}
EOF
        success "Clean-slate bay_map.json created."
    else
        warn "bay_map.json already exists. Skipping (your config is preserved)."
    fi

    # policy.json - only create if missing
    if [ ! -f "$CONFIG_DIR/policy.json" ]; then
        prompt_interactive_config

        info "Generating default policy.json..."
        # Safely compile the JSON structure using system Python to avoid shell escape issues
        export STATION_ID
        export WIPE_PORT
        export WIPE_PASSPHRASE
        export LAN_PASSPHRASE
        export SLACK_WEBHOOK
        
        "$PYTHON_BIN" -c "
import json, os
path = '$CONFIG_DIR/policy.json'
data = {
  'prewipe_spot_check': True,
  'post_erase_marker': True,
  'allow_method_override': True,
  'method_priority': {
    'nvme': ['crypto', 'block', 'overwrite'],
    'sas':  ['crypto', 'block', 'overwrite'],
    'sata': ['enhanced_secure_erase', 'secure_erase', 'overwrite']
  },
  'crypto_fail_retry_block': True,
  'health_soft_stop': True,
  'port': int(os.environ.get('WIPE_PORT', 5000)),
  'bind_address': '0.0.0.0',
  'station_id': os.environ.get('STATION_ID', 'wipe-station-01'),
  'wipe_passphrase': os.environ.get('WIPE_PASSPHRASE', ''),
  'slack_webhook_url': os.environ.get('SLACK_WEBHOOK', ''),
  'lan_passphrase': os.environ.get('LAN_PASSPHRASE', 'eraser123')
}
with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)
"
        unset STATION_ID
        unset WIPE_PORT
        unset WIPE_PASSPHRASE
        unset LAN_PASSPHRASE
        unset SLACK_WEBHOOK
        success "policy.json safely compiled."
    else
        warn "policy.json already exists. Skipping (your config is preserved)."
    fi
}

# -----------------------------------------------------------------------------
# STEP 6 - Create data and log directories
# -----------------------------------------------------------------------------

setup_directories() {
    info "Creating data and log directories..."

    mkdir -p "$LOG_DIR"
    mkdir -p "$ACTIVE_LOG_DIR"
    mkdir -p "$FAILED_LOG_DIR"
    mkdir -p "$DATA_DIR"
    mkdir -p "$CERT_DIR"

    # Create empty database placeholder
    if [ ! -f "$DB_PATH" ]; then
        touch "$DB_PATH"
        info "Database placeholder created."
    fi

    # Ensure correct ownership
    chown -R "$APP_USER:$APP_USER" "$LOG_DIR"
    chown -R "$APP_USER:$APP_USER" "$DATA_DIR"

    success "Directories ready."
}

# -----------------------------------------------------------------------------
# STEP 7 - Python virtual environment
# -----------------------------------------------------------------------------

setup_python() {
    info "Setting up Python virtual environment..."

    if [ ! -d "$VENV_DIR" ]; then
        $PYTHON_BIN -m venv "$VENV_DIR"
        success "Virtual environment created."
    else
        warn "Virtual environment already exists. Skipping creation."
    fi

    info "Installing Python dependencies..."
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        error "Virtual environment python binary not found at $VENV_DIR/bin/python"
    fi

    if ! "$VENV_DIR/bin/python" -c "import flask, flask_cors" >/dev/null 2>&1; then
        error "Virtual environment validation failed: cannot import flask/flask_cors"
    fi

    success "Python dependencies installed."
}

# -----------------------------------------------------------------------------
# STEP 8 - Set up sudo rules for disk commands
# -----------------------------------------------------------------------------

setup_sudo() {
    info "Configuring sudo rules for disk commands..."

    SUDOERS_FILE="/etc/sudoers.d/drive-eraser"
    TMP_SUDOERS_FILE="$(mktemp)"

    cat > "$TMP_SUDOERS_FILE" << EOF
# Drive Wipe Station - controlled disk command access
# Generated by install.sh - do not edit manually

$APP_USER ALL=(root) NOPASSWD: $SMARTCTL_PATH
$APP_USER ALL=(root) NOPASSWD: $HDPARM_PATH
$APP_USER ALL=(root) NOPASSWD: $NVME_PATH
$APP_USER ALL=(root) NOPASSWD: $SG_SANITIZE_PATH
$APP_USER ALL=(root) NOPASSWD: $SG_INQ_PATH
$APP_USER ALL=(root) NOPASSWD: $DD_PATH
$APP_USER ALL=(root) NOPASSWD: $LSBLK_PATH
$APP_USER ALL=(root) NOPASSWD: $LSHW_PATH
$APP_USER ALL=(root) NOPASSWD: $SYSTEMCTL_PATH
EOF

    chmod 440 "$TMP_SUDOERS_FILE"

    if visudo -cf "$TMP_SUDOERS_FILE"; then
        install -m 440 "$TMP_SUDOERS_FILE" "$SUDOERS_FILE"
        rm -f "$TMP_SUDOERS_FILE"
        success "Sudo rules configured."
    else
        rm -f "$TMP_SUDOERS_FILE"
        error "Sudoers file validation failed."
    fi
}

# -----------------------------------------------------------------------------
# STEP 9 - Set file permissions
# -----------------------------------------------------------------------------

set_permissions() {
    info "Setting file permissions..."

    chown -R "$APP_USER":"$APP_USER" "$INSTALL_DIR"

    # Logs and data writable by app user
    chmod -R 750 "$INSTALL_DIR"
    chmod -R 770 "$LOG_DIR"
    chmod -R 770 "$DATA_DIR"

    success "Permissions set."
}

# -----------------------------------------------------------------------------
# STEP 10 - Install systemd service
# -----------------------------------------------------------------------------

install_service() {
    info "Installing systemd service..."

    cp "$INSTALL_DIR/systemd/drive-eraser.service" \
       "/etc/systemd/system/$SERVICE_NAME.service"

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"

    success "Service installed and started."
}

# -----------------------------------------------------------------------------
# STEP 11 - Verify installation
# -----------------------------------------------------------------------------

verify_install() {
    info "Verifying installation..."

    sleep 2

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        warn "Virtual environment python missing: $VENV_DIR/bin/python"
    elif ! "$VENV_DIR/bin/python" -c "import flask, flask_cors" >/dev/null 2>&1; then
        warn "Virtual environment import check failed for flask/flask_cors"
    else
        success "Virtual environment check passed."
    fi

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        success "Service is running."
    else
        warn "Service may not have started. Check: journalctl -u $SERVICE_NAME"
    fi
}

# -----------------------------------------------------------------------------
# DONE
# -----------------------------------------------------------------------------

print_summary() {
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  Drive Wipe Station - Install Complete     ${NC}"
    echo -e "${GREEN}============================================${NC}"
    echo ""
    echo -e "  App location:   ${BLUE}$INSTALL_DIR${NC}"
    echo -e "  Config:         ${BLUE}$CONFIG_DIR${NC}"
    echo -e "  Logs:           ${BLUE}$LOG_DIR${NC}"
    echo -e "  Certificates:   ${BLUE}$CERT_DIR${NC}"
    echo ""
    echo -e "  Service status: ${BLUE}systemctl status $SERVICE_NAME${NC}"
    echo -e "  View logs:      ${BLUE}journalctl -u $SERVICE_NAME -f${NC}"
    echo ""
    echo -e "${YELLOW}  IMPORTANT: Edit config/bay_map.json to map${NC}"
    echo -e "${YELLOW}  physical bays to /dev/disk/by-path/ values.${NC}"
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo ""
}

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

main() {
    echo ""
    info "Starting Drive Wipe Station installation..."
    echo ""

    preflight
    install_packages
    resolve_command_paths
    create_user
    install_app
    setup_config
    write_command_paths_config
    setup_directories
    setup_python
    setup_sudo
    set_permissions
    install_service
    verify_install
    print_summary
}

main "$@"