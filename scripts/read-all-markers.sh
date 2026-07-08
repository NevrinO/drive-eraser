#!/bin/bash
# Read DWS markers from all attached block devices.
# Usage: sudo ./read-all-markers.sh
#
# Scans /dev for sd* and nvme* devices, reads the first 4KB block of each,
# and extracts/validates any DWS_MARKER_V1 found. Also fetches current
# SMART data_written_raw for comparison against the marker's stored value.

set -euo pipefail

MARKER_SIGNATURE="DWS_MARKER_V1"
MARKER_BLOCK_SIZE=4096

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=== DWS Marker Scanner ===${NC}"
echo "Date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo ""

# Gather all candidate devices: /dev/sd[a-z], /dev/sd[a-z][a-z], /dev/nvme*n1
DEVICES=()
for dev in /dev/sd[a-z] /dev/sd[a-z][a-z] /dev/nvme*n1; do
    [ -b "$dev" ] && DEVICES+=("$dev")
done

if [ ${#DEVICES[@]} -eq 0 ]; then
    echo "No block devices found."
    exit 0
fi

# Table header
printf "%-12s %-24s %-12s %-12s %-12s %-12s %s\n" \
    "DEVICE" "SERIAL" "MODEL" "METHOD" "STORED" "CURRENT" "STATUS"
printf "%s\n" "----------------------------------------------------------------------------------------------------------------------"

for dev in "${DEVICES[@]}"; do
    # Read first block
    raw_block=$(dd if="$dev" bs=${MARKER_BLOCK_SIZE} count=1 status=none 2>/dev/null || true)

    if [ -z "$raw_block" ]; then
        printf "%-12s %-24s %-12s %-12s %-12s %-12s %s\n" \
            "$dev" "-" "-" "-" "-" "-" "${RED}READ_ERROR${NC}"
        continue
    fi

    # Check for marker signature
    if ! echo "$raw_block" | grep -q "$MARKER_SIGNATURE" 2>/dev/null; then
        printf "%-12s %-24s %-12s %-12s %-12s %-12s %s\n" \
            "$dev" "-" "-" "-" "-" "-" "${YELLOW}NO_MARKER${NC}"
        continue
    fi

    # Extract JSON from the block (from first { to last })
    json_str=$(echo "$raw_block" | head -c ${MARKER_BLOCK_SIZE} | sed 's/\x00.*//' | tr -d '\n')

    # Parse fields with python for reliable JSON handling (pipe via stdin)
    parsed=$(echo "$json_str" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    serial = data.get("serial", "?")
    method = data.get("method", "?")
    stored = data.get("data_written_at_wipe", "?")
    job_id = data.get("job_id", "?")
    finished = data.get("finished_at", "?")
    print(f"{serial}|{method}|{stored}|{job_id}|{finished}")
except:
    print("PARSE_ERROR||||")
' 2>/dev/null || echo "PARSE_ERROR||||")

    IFS='|' read -r serial method stored job_id finished <<< "$parsed"

    if [ "$serial" = "PARSE_ERROR" ]; then
        printf "%-12s %-24s %-12s %-12s %-12s %-12s %s\n" \
            "$dev" "?" "?" "?" "?" "?" "${RED}JSON_PARSE_ERROR${NC}"
        continue
    fi

    # Get current SMART data_written_raw
    current="-"
    diff_str="-"
    status_color="${GREEN}"
    status_text="PRISTINE"

    smart_raw=$(smartctl -j -x "$dev" 2>/dev/null || true)
    if [ -n "$smart_raw" ]; then
        # Extract data_written_raw using same logic as smart_data_parsing.py
        current=$(echo "$smart_raw" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    scsi_log = data.get("scsi_error_counter_log", {})
    if "write" in scsi_log:
        gb = scsi_log["write"].get("gigabytes_processed")
        if gb is not None:
            written_bytes = int(float(gb) * 10**9)
            written_raw = int(written_bytes / 512)
            print(written_raw)
            sys.exit(0)
    nvme = data.get("nvme_smart_health_information_log", {})
    if nvme.get("data_units_written") is not None:
        print(nvme["data_units_written"])
        sys.exit(0)
    attrs = data.get("ata_smart_attributes", {}).get("table", [])
    for attr in attrs:
        if attr.get("id") == 241:
            print(attr.get("raw", {}).get("value", ""))
            sys.exit(0)
    print("")
except:
    print("")
' 2>/dev/null || echo "")

        if [ -n "$current" ] && [ "$current" != "" ] && [ "$stored" != "?" ]; then
            diff=$((current - stored))
            # Determine tolerance based on interface
            iface=$(echo "$smart_raw" | python3 -c '
import json, sys
data = json.load(sys.stdin)
proto = data.get("device", {}).get("protocol", "")
if "SCSI" in proto or "SAS" in proto:
    print("sas")
elif "NVMe" in proto:
    print("nvme")
else:
    print("sata")
' 2>/dev/null || echo "sata")

            if [ "$iface" = "sas" ]; then
                tolerance=100000
            elif [ "$iface" = "nvme" ]; then
                tolerance=4
            else
                tolerance=4096
            fi

            diff_str="$diff (tol: $tolerance)"

            if [ "$diff" -lt 0 ]; then
                status_color="${RED}"
                status_text="NEGATIVE_DIFF"
            elif [ "$diff" -gt "$tolerance" ]; then
                status_color="${RED}"
                status_text="WRITTEN_SINCE_WIPE"
            fi
        fi
    fi

    # Get model from smartctl
    model=$(echo "$smart_raw" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get("model_name", "?")[:12])
except:
    print("?")
' 2>/dev/null || echo "?")

    printf "%-12s %-24s %-12s %-12s %-12s %-12s ${status_color}%s${NC}\n" \
        "$dev" "${serial:0:24}" "${model:0:12}" "${method:0:12}" \
        "${stored:0:12}" "${current:0:12}" "$status_text"
done

echo ""
echo -e "${CYAN}=== Detail ===${NC}"
echo ""

for dev in "${DEVICES[@]}"; do
    raw_block=$(dd if="$dev" bs=${MARKER_BLOCK_SIZE} count=1 status=none 2>/dev/null || true)
    if [ -z "$raw_block" ]; then continue; fi
    if ! echo "$raw_block" | grep -q "$MARKER_SIGNATURE" 2>/dev/null; then continue; fi

    json_str=$(echo "$raw_block" | head -c ${MARKER_BLOCK_SIZE} | sed 's/\x00.*//' | tr -d '\n')
    echo -e "${CYAN}--- $dev ---${NC}"
    echo "$json_str" | python3 -m json.tool 2>/dev/null || echo "$json_str"
    echo ""
done
