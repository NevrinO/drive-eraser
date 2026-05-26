#!/bin/bash
# =============================================================================
# Drive Wipe Station - Diagnostic Log Exporter (CLI Fallback)
# =============================================================================
# Usage:
#   sudo bash scripts/export-logs.sh [output_directory]
# =============================================================================

set -e

APP_NAME="drive-eraser"
INSTALL_DIR="/opt/drive-eraser"
DATA_DIR="$INSTALL_DIR/data"
LOGS_DIR="$DATA_DIR/logs"
FAILED_LOGS_DIR="$LOGS_DIR/failed"
CONFIG_DIR="$INSTALL_DIR/config"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
HOSTNAME="$(hostname)"
BUNDLE_NAME="support-bundle-${HOSTNAME}-${TIMESTAMP}"
WORKSPACE_DIR="/tmp/${BUNDLE_NAME}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Requires root because it runs privileged hardware commands (lsblk, lshw, smartctl)
if [ "$EUID" -ne 0 ]; then
    error "Please run this script with sudo or as root."
fi

info "Creating temporary workspace at $WORKSPACE_DIR..."
mkdir -p "$WORKSPACE_DIR"

# 1. Harvest Hardware Environment Details
info "Gathering hardware configurations..."
{
    echo "=== SYSTEM LSBLK GEOMETRY ==="
    lsblk -J || echo "lsblk failed"
    echo ""
    echo "=== LSHW STORAGE DETAILS ==="
    lshw -class storage -class disk || echo "lshw failed"
} > "$WORKSPACE_DIR/hardware_environment.txt"

# 2. Harvest System Resource Metrics
info "Gathering system resources metrics..."
{
    echo "Hostname: $HOSTNAME"
    echo "Kernel: $(uname -r)"
    echo "Uptime: $(uptime)"
    echo "Memory Usage:"
    free -h || echo "free failed"
    echo ""
    echo "Disk Space Usage:"
    df -h "$DATA_DIR" || echo "df failed"
} > "$WORKSPACE_DIR/system_metrics.txt"

# 3. Copy and Redact Config Files
info "Processing configuration files..."
if [ -f "$CONFIG_DIR/policy.json" ]; then
    # Use python to redact sensitive fields safely
    python3 -c "
import json
with open('$CONFIG_DIR/policy.json', 'r') as f:
    data = json.load(f)
for key in ['wipe_passphrase', 'slack_webhook_url', 'lan_passphrase']:
    if key in data:
        data[key] = '[REDACTED]'
with open('$WORKSPACE_DIR/redacted_policy.json', 'w') as f:
    json.dump(data, f, indent=2)
" || cp "$CONFIG_DIR/policy.json" "$WORKSPACE_DIR/redacted_policy_UNREDACTED_FALLBACK.json"
fi

if [ -f "$CONFIG_DIR/bay_map.json" ]; then
    cp "$CONFIG_DIR/bay_map.json" "$WORKSPACE_DIR/bay_map.json"
fi

# 4. Copy active/failed/app logs
info "Packaging application log streams..."
if [ -f "$LOGS_DIR/app.log" ]; then
    cp "$LOGS_DIR/app.log" "$WORKSPACE_DIR/app.log"
fi
if [ -d "$FAILED_LOGS_DIR" ]; then
    cp -r "$FAILED_LOGS_DIR" "$WORKSPACE_DIR/failed_logs" || true
fi

# 5. Compile the Compressed Tarball
TARBALL_PATH="/tmp/${BUNDLE_NAME}.tar.gz"
info "Compiling compressed diagnostic bundle..."
tar -czf "$TARBALL_PATH" -C "/tmp" "$BUNDLE_NAME"
rm -rf "$WORKSPACE_DIR"

# 6. Determine Export Target (Scan for mounted USB devices first)
EXPORT_DIR="$1"
if [ -z "$EXPORT_DIR" ]; then
    # Automatically scan for mounted USB drives under common mount locations
    USB_PATH=""
    for mount_pt in /media/*/* /mnt/* /media/*; do
        if [ -d "$mount_pt" ] && [ -w "$mount_pt" ]; then
            # Verify it is not the main root partition
            if ! df "$mount_pt" | grep -q "/$"; then
                USB_PATH="$mount_pt"
                break
            fi
        fi
    done

    if [ -n "$USB_PATH" ]; then
        EXPORT_DIR="$USB_PATH"
        info "Writable USB mount point detected at: $EXPORT_DIR"
    else
        # Fall back to the running technician's home folder or fallback
        if [ -n "$SUDO_USER" ]; then
            EXPORT_DIR="/home/$SUDO_USER"
        else
            EXPORT_DIR="/root"
        fi
        warn "No mounted USB device detected. Exporting to: $EXPORT_DIR"
    fi
fi

# 7. Relocate Bundle and Output Summary
mkdir -p "$EXPORT_DIR"
FINAL_DEST="$EXPORT_DIR/${BUNDLE_NAME}.tar.gz"
mv "$TARBALL_PATH" "$FINAL_DEST"

# Ensure non-root ownership if copied to a user's home directory
if [ -n "$SUDO_USER" ] && [[ "$EXPORT_DIR" == /home/* ]]; then
    chown "$SUDO_USER:$SUDO_USER" "$FINAL_DEST"
fi

echo ""
success "========================================================="
success "  Diagnostic Log Export Complete!"
success "========================================================="
success "  Support Bundle: $FINAL_DEST"
success "  Size:           $(du -sh "$FINAL_DEST" | cut -f1)"
success "========================================================="
echo ""