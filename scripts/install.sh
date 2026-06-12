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
BLOCKDEV_PATH=""

# Default config parameters (overwritten if run interactively)
STATION_ID="wipe-station-01"
WIPE_PORT=5000
WIPE_PASSPHRASE=""
STRICT_AUDIT_MODE="true"
CRYPTO_VERIFICATION_MODE="conservative_probe"
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
    BLOCKDEV_PATH="$(find_cmd_path blockdev)"

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
  "systemctl": "$SYSTEMCTL_PATH",
  "blockdev": "$BLOCKDEV_PATH"
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
    # python3-dev: Required for building Pillow 11.x from source on Ubuntu 26.04
    # (Pillow C extensions need build headers when pre-built wheels unavailable)
    apt-get install -y \
        python3 \
        python3-dev \
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
        rsync \
        libjpeg-dev \
        zlib1g-dev \
        libfreetype6-dev \
        liblcms2-dev \
        libwebp-dev \
        libopenjp2-7-dev \
        libtiff5-dev

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

        # 4. Prompt for strict audit mode and Cryptographic Wipe Passphrase (Salt Signer)
        echo -e "  Strict Audit Mode requires signed sanitization certificates and is recommended for production."
        echo -e -n "  Enable Strict Audit Mode? [Y/n]: "
        local strict_choice
        read -r strict_choice
        if [[ "$strict_choice" =~ ^[Nn]$ ]]; then
            STRICT_AUDIT_MODE="false"
            warn "Strict Audit Mode disabled for this installation."
        else
            STRICT_AUDIT_MODE="true"
            success "Strict Audit Mode enabled."
        fi
        echo ""

        echo -e "  Entering a Cryptographic Passphrase enables secure HMAC-SHA256 certificate signing."
        if [ "$STRICT_AUDIT_MODE" = "true" ]; then
            echo -e "  A passphrase is required because Strict Audit Mode is enabled."
        else
            echo -e "  Leave blank to run in Unauthenticated State."
        fi
        while true; do
            if [ "$STRICT_AUDIT_MODE" = "true" ]; then
                echo -e -n "  Enter Cryptographic Passphrase [Required]: "
            else
                echo -e -n "  Enter Cryptographic Passphrase [Optional - Press Enter to Skip]: "
            fi
            local pass=""
            local confirm=""
            read -r -s pass
            echo ""
            
            if [ -z "$pass" ]; then
                if [ "$STRICT_AUDIT_MODE" = "true" ]; then
                    warn "A passphrase is required while Strict Audit Mode is enabled."
                    continue
                else
                    info "Skipping Cryptographic Passphrase setup."
                    break
                fi
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

    # Critical #2: Detect server IP for CORS origins
    local detected_ip=""
    if command -v ip &>/dev/null; then
        detected_ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' | head -n1)
    fi
    if [ -z "$detected_ip" ] && command -v hostname &>/dev/null; then
        detected_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi
    if [ -z "$detected_ip" ]; then
        detected_ip="127.0.0.1"
    fi
    local cors_origin="http://${detected_ip}:${WIPE_PORT}"

     # bay_map.json - only create if missing
    if [ ! -f "$CONFIG_DIR/bay_map.json" ]; then
        info "Creating default clean-slate bay_map.json..."
        cat > "$CONFIG_DIR/bay_map.json" << 'EOF'
{
  "bay0": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay",
    "by_path": null
  },
  "bay1": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay",
    "by_path": null
  },
  "bay2": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay",
    "by_path": null
  },
  "bay3": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay",
    "by_path": null
  },
  "bay4": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay",
    "by_path": null
  },
  "bay5": {
    "role": "wipe",
    "locked": false,
    "type": "sas_sata",
    "label": "Work Bay",
    "by_path": null
  },
  "bay6": {
    "role": "wipe",
    "locked": false,
    "type": "u2",
    "label": "Work Bay",
    "by_path": null
  },
  "bay7": {
    "role": "wipe",
    "locked": false,
    "type": "u2",
    "label": "Work Bay",
    "by_path": null
  },
  "bay8": {
    "role": "wipe",
    "locked": false,
    "type": "u2",
    "label": "Work Bay",
    "by_path": null
  },
  "bay9": {
    "role": "wipe",
    "locked": false,
    "type": "u2",
    "label": "Work Bay",
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

        if [ "$STRICT_AUDIT_MODE" = "true" ] && [ -z "$WIPE_PASSPHRASE" ]; then
            STRICT_AUDIT_MODE="false"
            warn "Strict Audit Mode disabled because no passphrase was provided."
        fi

        info "Generating default policy.json..."
        # Safely compile the JSON structure using system Python to avoid shell escape issues
        export STATION_ID
        export WIPE_PORT
        export WIPE_PASSPHRASE
        export STRICT_AUDIT_MODE
        export CRYPTO_VERIFICATION_MODE
        export LAN_PASSPHRASE
        export SLACK_WEBHOOK
        export CORS_ORIGIN
        
        "$PYTHON_BIN" -c "
import json, os
path = '$CONFIG_DIR/policy.json'
cors_origin = os.environ.get('CORS_ORIGIN', 'http://127.0.0.1:5000')
data = {
  'prewipe_spot_check': True,
  'post_erase_marker': True,
  'allow_method_override': True,
  'method_priority': {
    'nvme': ['crypto', 'block', 'overwrite'],
    'sas':  ['crypto', 'block', 'overwrite'],
    'sata': ['crypto', 'block', 'overwrite']
  },
  'crypto_fail_retry_block': True,
  'strict_audit_mode': os.environ.get('STRICT_AUDIT_MODE', 'true').lower() == 'true',
  'crypto_verification_mode': os.environ.get('CRYPTO_VERIFICATION_MODE', 'conservative_probe'),
  'health_soft_stop': True,
  'port': int(os.environ.get('WIPE_PORT', 5000)),
  'bind_address': '0.0.0.0',
  'station_id': os.environ.get('STATION_ID', 'wipe-station-01'),
  'wipe_passphrase': os.environ.get('WIPE_PASSPHRASE', ''),
  'slack_webhook_url': os.environ.get('SLACK_WEBHOOK', ''),
  'lan_passphrase': os.environ.get('LAN_PASSPHRASE', 'eraser123'),
  'allowed_cors_origins': ['http://localhost:5000', 'http://127.0.0.1:5000', cors_origin],
  'triage_thresholds': {
    'ssd_new_poh_threshold': 500,
    'ssd_high_poh_threshold': 40000,
    'hdd_new_poh_threshold': 500,
    'hdd_high_poh_threshold': 40000,
    'health_score_destroy_threshold': 20,
    'health_score_scratch_threshold': 60,
    'ssd_remaining_life_destroy_threshold': 10,
    'ssd_remaining_life_scratch_threshold': 60,
    'ssd_remaining_life_good_threshold': 80,
    'ssd_new_fdw_threshold': 0.06,
    'hdd_new_fdw_threshold': 1.0,
    'hdd_heavy_fdw_threshold': 150,
    'realloc_raw_new_threshold': 0,
    'pending_sectors_destroy_threshold': 10,
    'pending_sectors_scratch_threshold': 10
  }
}
with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)
"
        unset WIPE_PASSPHRASE
        unset STRICT_AUDIT_MODE
        unset CRYPTO_VERIFICATION_MODE
        unset LAN_PASSPHRASE
        unset SLACK_WEBHOOK
        success "policy.json safely compiled."
    else
        warn "policy.json already exists. Skipping (your config is preserved)."
    fi

    # layout_templates.json - create if missing
    # Note: Backend handles corrupted files via hash validation and fallback to DEFAULT_TEMPLATES
    if [ ! -f "$CONFIG_DIR/layout_templates.json" ]; then
        info "Creating default layout_templates.json..."
        # Use Python to generate JSON and hash to match backend's json.dumps() formatting exactly
        python3 << PYTHON_SCRIPT
import json
import hashlib

DEFAULT_TEMPLATES = {
    "dell_r320_4bay": {
        "id": "dell_r320_4bay",
        "name": "Dell R320 4-Bay (3.5\")",
        "vendor": "Dell",
        "rows": 1,
        "cols": 4,
        "bay_count": 4,
        "traversal_preset": "top_left_down_then_across"
    },
    "dell_r440_10bay": {
        "id": "dell_r440_10bay",
        "name": "Dell R440 10-Bay (2.5\")",
        "vendor": "Dell",
        "rows": 2,
        "cols": 5,
        "bay_count": 10,
        "traversal_preset": "top_left_down_then_across"
    }
}

data = {"templates": DEFAULT_TEMPLATES}
json_content = json.dumps(data, indent=2)
content_hash = hashlib.sha256(json_content.encode('utf-8')).hexdigest()

config_dir = '$CONFIG_DIR'
with open(f"{config_dir}/layout_templates.json", 'w', encoding='utf-8') as f:
    f.write(json_content)
with open(f"{config_dir}/layout_templates.json.sha256", 'w', encoding='utf-8') as f:
    f.write(content_hash)
PYTHON_SCRIPT
        success "Default layout_templates.json and hash file created."
    else
        info "layout_templates.json already exists. Preserving custom templates."
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

    if ! "$VENV_DIR/bin/python" -c "import flask, flask_cors, bs4, PIL" >/dev/null 2>&1; then
        error "Virtual environment validation failed: cannot import flask/flask_cors/bs4/PIL"
    fi

    success "Python dependencies installed."
}

# -----------------------------------------------------------------------------
# STEP 8 - Set up sudo rules for disk commands
# -----------------------------------------------------------------------------
# IMPORTANT: If you modify the sudoers rules here, you MUST also update
# scripts/update.sh setup_sudo() to keep both files in sync.
# -----------------------------------------------------------------------------

setup_sudo() {
    info "Configuring sudo rules for disk commands..."

    SUDOERS_FILE="/etc/sudoers.d/drive-eraser"
    TMP_SUDOERS_FILE="$(mktemp)"

    cat > "$TMP_SUDOERS_FILE" << EOF
# Drive Wipe Station - controlled disk command access
# Generated by install.sh - do not edit manually
# Suppress syslog logging for disk utility commands to reduce journalctl spam
# Application-level audit trail is maintained in SQLite database (data/wipes.db)

Defaults!$SMARTCTL_PATH !syslog
Defaults!$HDPARM_PATH !syslog
Defaults!$NVME_PATH !syslog
Defaults!$SG_SANITIZE_PATH !syslog
Defaults!$SG_INQ_PATH !syslog
Defaults!$DD_PATH !syslog
Defaults!$LSBLK_PATH !syslog
Defaults!$LSHW_PATH !syslog
Defaults!$SYSTEMCTL_PATH !syslog
Defaults!$BLOCKDEV_PATH !syslog

$APP_USER ALL=(root) NOPASSWD: $SMARTCTL_PATH
$APP_USER ALL=(root) NOPASSWD: $HDPARM_PATH
$APP_USER ALL=(root) NOPASSWD: $NVME_PATH
$APP_USER ALL=(root) NOPASSWD: $SG_SANITIZE_PATH
$APP_USER ALL=(root) NOPASSWD: $SG_INQ_PATH
$APP_USER ALL=(root) NOPASSWD: $DD_PATH
$APP_USER ALL=(root) NOPASSWD: $LSBLK_PATH
$APP_USER ALL=(root) NOPASSWD: $LSHW_PATH
$APP_USER ALL=(root) NOPASSWD: $SYSTEMCTL_PATH
$APP_USER ALL=(root) NOPASSWD: $BLOCKDEV_PATH
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

detect_system_resources() {
    info "Detecting system resources for resource limits..."

    # High #10: Detect memory (in GB)
    local total_mem_kb
    total_mem_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    local total_mem_gb=$((total_mem_kb / 1024 / 1024))

    # Calculate memory limits: MemoryLimit=50%, MemoryMax=75% (floor: 2G/4G)
    local memory_limit=$((total_mem_gb / 2))
    if [ $memory_limit -lt 2 ]; then
        memory_limit=2
    fi
    local memory_max=$((total_mem_gb * 3 / 4))
    if [ $memory_max -lt 4 ]; then
        memory_max=4
    fi

    # High #10: Detect CPU cores
    local cpu_cores
    cpu_cores=$(nproc)
    # Calculate CPU quota: (cores-1)×100% (minimum 100%)
    local cpu_quota=$(( (cpu_cores - 1) * 100 ))
    if [ $cpu_quota -lt 100 ]; then
        cpu_quota=100
    fi

    # High #10: File descriptor limit (fixed)
    local limit_nofile=65536

    info "System resources detected: ${total_mem_gb}GB RAM, ${cpu_cores} CPU cores"
    info "Resource limits: MemoryLimit=${memory_limit}G, MemoryMax=${memory_max}G, CPUQuota=${cpu_quota}%, LimitNOFILE=${limit_nofile}"

    # Export for use in service file generation
    export MEMORY_LIMIT="${memory_limit}G"
    export MEMORY_MAX="${memory_max}G"
    export CPU_QUOTA="${cpu_quota}%"
    export LIMIT_NOFILE="$limit_nofile"
}

install_service() {
    info "Installing systemd service..."

    detect_system_resources

    # Generate service file with dynamic resource limits
    cat > "/etc/systemd/system/$SERVICE_NAME.service" << EOF
[Unit]
Description=Drive Wipe Station
Documentation=https://github.com/NevrinO/drive-eraser
After=network.target
Wants=network.target

[Service]
# Run as dedicated system user
User=$APP_USER
Group=$APP_USER

# Application location
WorkingDirectory=$INSTALL_DIR

# Start command
ExecStart=$VENV_DIR/bin/python backend/app.py

# Restart behavior
Restart=on-failure
RestartSec=5s
StartLimitIntervalSec=60
StartLimitBurst=3

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=drive-eraser

# Security hardening
NoNewPrivileges=false
PrivateTmp=true
ProtectSystem=full

# High #10: Dynamic resource limits based on system detection
# Memory limits: 50% soft limit (floor 2G), 75% hard limit (floor 4G)
MemoryLimit=$MEMORY_LIMIT
MemoryMax=$MEMORY_MAX
# CPU quota: (cores-1)×100% (minimum 100%)
CPUQuota=$CPU_QUOTA
# File descriptor limit
LimitNOFILE=$LIMIT_NOFILE

# Prefixing with '-' prevents systemd from crashing if folders are temporarily missing
ReadWritePaths=-$INSTALL_DIR/data
ReadWritePaths=-$INSTALL_DIR/logs

# Environment
Environment=FLASK_ENV=production
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"

    success "Service installed and started with dynamic resource limits."
}

# -----------------------------------------------------------------------------
# STEP 11 - Verify installation
# -----------------------------------------------------------------------------

verify_install() {
    info "Verifying installation..."

    sleep 2

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        warn "Virtual environment python missing: $VENV_DIR/bin/python"
    elif ! "$VENV_DIR/bin/python" -c "import flask, flask_cors, bs4" >/dev/null 2>&1; then
        warn "Virtual environment import check failed for flask/flask_cors/bs4"
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

    local detected_ip=""
    if command -v ip &>/dev/null; then
        detected_ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' | head -n1)
    fi
    if [ -z "$detected_ip" ] && command -v hostname &>/dev/null; then
        detected_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi
    if [ -z "$detected_ip" ]; then
        detected_ip="<server-ip>"
    fi

    echo -e "${GREEN}  Web UI Access:${NC}"
    echo -e "    Open browser to: ${BLUE}http://${detected_ip}:${WIPE_PORT}${NC}"
    echo -e "    Or locally:      ${BLUE}http://127.0.0.1:${WIPE_PORT}${NC}"
    echo -e "    Configure bay mapping via System Administration tab"
    echo ""
    echo -e "${YELLOW}  Documentation:${NC}"
    echo -e "    Guides are available inside the web UI (Audit / Help tabs)"
    echo -e "    Local copies: ${BLUE}$INSTALL_DIR/docs/${NC} (requires admin/sudo access)"
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