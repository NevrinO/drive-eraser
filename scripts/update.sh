#!/bin/bash
# =============================================================================
# Drive Wipe Station - Update Script
# =============================================================================
# Usage:
#   bash scripts/update.sh
#
# Run as root or with sudo.
# Preserves all config files and data.
# =============================================================================

set -e

APP_USER="wipestation"
INSTALL_DIR="/opt/drive-eraser"
SERVICE_NAME="drive-eraser"
VENV_DIR="$INSTALL_DIR/venv"
CONFIG_DIR="$INSTALL_DIR/config"
DATA_DIR="$INSTALL_DIR/data"
LOG_DIR="$DATA_DIR/logs"
ACTIVE_LOG_DIR="$LOG_DIR/active"
FAILED_LOG_DIR="$LOG_DIR/failed"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$INSTALL_DIR/backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

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

DRY_RUN=false
NO_RESTART=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

print_usage() {
    cat << EOF
Usage: bash scripts/update.sh [--no-restart] [--dry-run]

Options:
  --no-restart   Apply update steps but skip service restart and status verification.
  --dry-run      Print the actions that would run without making system changes.
  -h, --help     Show this help message.
EOF
}

run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        info "[dry-run] $*"
    else
        "$@"
    fi
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --no-restart)
                NO_RESTART=true
                ;;
            --dry-run)
                DRY_RUN=true
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                error "Unknown argument: $1"
                ;;
        esac
        shift
    done
}

require_root() {
    if [ "$DRY_RUN" = true ]; then
        warn "Dry-run mode: skipping root requirement check."
        return
    fi
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

preflight() {
    info "Running pre-flight checks..."
    require_root

    if [ ! -d "$INSTALL_DIR" ]; then
        error "Install directory not found: $INSTALL_DIR"
    fi

    if [ ! -d "$REPO_DIR/backend" ]; then
        error "Repository source not found at: $REPO_DIR"
    fi

    success "Pre-flight checks passed."
}

backup_config() {
    info "Backing up config files..."
    run_cmd mkdir -p "$BACKUP_DIR/$TIMESTAMP"

    if [ -f "$CONFIG_DIR/bay_map.json" ]; then
        run_cmd cp "$CONFIG_DIR/bay_map.json" "$BACKUP_DIR/$TIMESTAMP/bay_map.json"
    fi
    if [ -f "$CONFIG_DIR/policy.json" ]; then
        run_cmd cp "$CONFIG_DIR/policy.json" "$BACKUP_DIR/$TIMESTAMP/policy.json"
    fi
    if [ -f "$CONFIG_DIR/command_paths.json" ]; then
        run_cmd cp "$CONFIG_DIR/command_paths.json" "$BACKUP_DIR/$TIMESTAMP/command_paths.json"
    fi

    success "Config backup completed: $BACKUP_DIR/$TIMESTAMP"
}

sync_app_files() {
    info "Syncing application files..."

    run_cmd rsync -a \
        --delete \
        --exclude='venv/' \
        --exclude='data/' \
        --exclude='logs/' \
        --exclude='backups/' \
        --exclude='config/bay_map.json' \
        --exclude='config/policy.json' \
        --exclude='config/command_paths.json' \
        "$REPO_DIR/" "$INSTALL_DIR/"

    success "Application files synced."
}

write_command_paths_config() {
    info "Writing command path config..."

    run_cmd mkdir -p "$CONFIG_DIR"

    if [ "$DRY_RUN" = true ]; then
        info "[dry-run] write $CONFIG_DIR/command_paths.json"
    else
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
    fi

    success "Command path config written."
}

setup_python() {
    info "Updating Python environment..."

    if [ ! -x "$VENV_DIR/bin/python" ]; then
        error "Virtual environment python binary not found at $VENV_DIR/bin/python"
    fi

    run_cmd "$VENV_DIR/bin/pip" install --upgrade pip -q
    run_cmd "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

    if [ "$DRY_RUN" = false ] && ! "$VENV_DIR/bin/python" -c "import flask, flask_cors" >/dev/null 2>&1; then
        error "Virtual environment validation failed: cannot import flask/flask_cors"
    fi

    success "Python environment updated."
}

# IMPORTANT: If you modify the sudoers rules here, you MUST also update
# scripts/install.sh setup_sudo() to keep both files in sync.
# -----------------------------------------------------------------------------
setup_sudo() {
    info "Refreshing sudo rules for disk commands..."

    SUDOERS_FILE="/etc/sudoers.d/drive-eraser"
    TMP_SUDOERS_FILE="$(mktemp)"

    cat > "$TMP_SUDOERS_FILE" << EOF
# Drive Wipe Station - controlled disk command access
# Generated by update.sh - do not edit manually

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

    run_cmd chmod 440 "$TMP_SUDOERS_FILE"

    if [ "$DRY_RUN" = true ]; then
        info "[dry-run] validate and install sudoers: $SUDOERS_FILE"
        run_cmd rm -f "$TMP_SUDOERS_FILE"
        success "Sudo rules update simulated."
        return
    fi

    if visudo -cf "$TMP_SUDOERS_FILE"; then
        install -m 440 "$TMP_SUDOERS_FILE" "$SUDOERS_FILE"
        rm -f "$TMP_SUDOERS_FILE"
        success "Sudo rules updated."
    else
        rm -f "$TMP_SUDOERS_FILE"
        error "Sudoers file validation failed."
    fi
}

set_permissions() {
    info "Setting file permissions..."

    run_cmd mkdir -p "$DATA_DIR" "$LOG_DIR" "$ACTIVE_LOG_DIR" "$FAILED_LOG_DIR"
    run_cmd chown -R "$APP_USER":"$APP_USER" "$INSTALL_DIR"
    run_cmd chmod -R 750 "$INSTALL_DIR"
    run_cmd chmod -R 770 "$DATA_DIR"
    run_cmd chmod -R 770 "$LOG_DIR"

    success "Permissions set."
}

restart_service() {
    if [ "$NO_RESTART" = true ]; then
        warn "Skipping service restart (--no-restart)."
        return
    fi

    info "Reloading and restarting service..."

    run_cmd cp "$INSTALL_DIR/systemd/drive-eraser.service" \
       "/etc/systemd/system/$SERVICE_NAME.service"

    run_cmd systemctl daemon-reload
    run_cmd systemctl enable "$SERVICE_NAME"
    run_cmd systemctl restart "$SERVICE_NAME"

    success "Service restarted."
}

verify_update() {
    if [ "$NO_RESTART" = true ]; then
        warn "Skipping service verification because restart was skipped."
        return
    fi

    info "Verifying update..."

    run_cmd sleep 2

    if [ "$DRY_RUN" = true ]; then
        info "[dry-run] check service active state: $SERVICE_NAME"
        success "Service verification simulated."
        return
    fi

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        success "Service is running."
    else
        warn "Service may not have started. Check: journalctl -u $SERVICE_NAME"
    fi
}

print_summary() {
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  Drive Wipe Station - Update Complete      ${NC}"
    echo -e "${GREEN}============================================${NC}"
    echo ""
    echo -e "  Install dir:    ${BLUE}$INSTALL_DIR${NC}"
    echo -e "  Backup dir:     ${BLUE}$BACKUP_DIR/$TIMESTAMP${NC}"
    echo -e "  Service status: ${BLUE}systemctl status $SERVICE_NAME${NC}"
    echo -e "  View logs:      ${BLUE}journalctl -u $SERVICE_NAME -f${NC}"
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo ""
}

main() {
    parse_args "$@"

    echo "==============================================="
    echo "  Drive Wipe Station - Update"
    echo "==============================================="

    if [ "$DRY_RUN" = true ]; then
        warn "Running in dry-run mode. No system changes will be made."
    fi
    if [ "$NO_RESTART" = true ]; then
        warn "Service restart and verification are disabled."
    fi

    preflight
    backup_config
    sync_app_files
    resolve_command_paths
    write_command_paths_config
    setup_python
    setup_sudo
    set_permissions
    restart_service
    verify_update
    print_summary
}

main "$@"