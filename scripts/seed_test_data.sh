#!/bin/bash
set -euo pipefail

DEVICE=""
SIZE_MB=256
PATTERN="random"
CONFIRM=""

usage() {
    cat <<EOF
Usage: sudo bash scripts/seed_test_data.sh --device /dev/sdX [--size-mb 256] [--pattern random|zero] --confirm "SEED /dev/sdX"
EOF
}

error() {
    echo "[ERROR] $1" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --device)
            DEVICE="${2:-}"
            shift 2
            ;;
        --size-mb)
            SIZE_MB="${2:-}"
            shift 2
            ;;
        --pattern)
            PATTERN="${2:-}"
            shift 2
            ;;
        --confirm)
            CONFIRM="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown argument: $1"
            ;;
    esac
done

[ "$(id -u)" -eq 0 ] || error "Run as root or with sudo"
[ -n "$DEVICE" ] || error "--device is required"
[ -b "$DEVICE" ] || error "Device is not a block device: $DEVICE"
[[ "$SIZE_MB" =~ ^[0-9]+$ ]] || error "--size-mb must be an integer"
[ "$SIZE_MB" -ge 1 ] || error "--size-mb must be at least 1"
[ "$SIZE_MB" -le 102400 ] || error "--size-mb must be <= 102400"
[ "$PATTERN" = "random" ] || [ "$PATTERN" = "zero" ] || error "--pattern must be random or zero"

EXPECTED_CONFIRM="SEED $DEVICE"
[ "$CONFIRM" = "$EXPECTED_CONFIRM" ] || error "Confirmation mismatch. Use: --confirm \"$EXPECTED_CONFIRM\""

if lsblk -nr -o MOUNTPOINT "$DEVICE" | grep -qE '.'; then
    error "Refusing mounted device: $DEVICE"
fi

ROOT_SOURCE="$(findmnt -n -o SOURCE / || true)"
ROOT_PARENT=""
if [ -n "$ROOT_SOURCE" ] && [ -b "$ROOT_SOURCE" ]; then
    ROOT_PARENT="$(lsblk -no PKNAME "$ROOT_SOURCE" 2>/dev/null || true)"
fi

TARGET_BASE="$(basename "$DEVICE")"
ROOT_BASE="$(basename "$ROOT_SOURCE" 2>/dev/null || true)"
if [ "$TARGET_BASE" = "$ROOT_BASE" ] || [ -n "$ROOT_PARENT" ] && [ "$TARGET_BASE" = "$ROOT_PARENT" ]; then
    error "Refusing root OS disk target: $DEVICE"
fi

echo "Seeding test data to $DEVICE"
echo "Pattern: $PATTERN"
echo "Size: ${SIZE_MB}MiB"

if [ "$PATTERN" = "random" ]; then
    dd if=/dev/urandom of="$DEVICE" bs=1M count="$SIZE_MB" conv=fsync status=progress
else
    dd if=/dev/zero of="$DEVICE" bs=1M count="$SIZE_MB" conv=fsync status=progress
fi

META="DWS_TEST_DATA|ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)|device=$DEVICE|size_mb=$SIZE_MB|pattern=$PATTERN"
printf "%s\n" "$META" | dd of="$DEVICE" bs=4096 count=1 conv=fsync,notrunc status=none

sync

echo "Seed write completed for $DEVICE"
