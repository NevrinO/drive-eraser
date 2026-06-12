#!/bin/bash
# Kill all running and queued erase jobs
# Usage: ./kill-all-jobs.sh [admin_passphrase]

set -e

ADMIN_URL="${ADMIN_URL:-http://localhost:5000}"
PASSPHRASE="${1:-}"

if [ -z "$PASSPHRASE" ]; then
    # Try localhost without auth first
    response=$(curl -s -w "\n%{http_code}" -X POST "${ADMIN_URL}/api/admin/jobs/kill-all" 2>/dev/null || echo -e "\n000")
else
    # Use passphrase for remote access
    session_token=$(echo -n "$PASSPHRASE" | sha256sum | cut -d' ' -f1)
    response=$(curl -s -w "\n%{http_code}" -X POST \
        -b "admin_session=${session_token}" \
        "${ADMIN_URL}/api/admin/jobs/kill-all" 2>/dev/null || echo -e "\n000")
fi

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')

if [ "$http_code" = "200" ]; then
    echo "Success: $body"
    exit 0
elif [ "$http_code" = "401" ]; then
    echo "Error: Authentication required. Provide admin passphrase as argument."
    echo "Usage: $0 <admin_passphrase>"
    exit 1
elif [ "$http_code" = "000" ]; then
    echo "Error: Could not connect to server at ${ADMIN_URL}"
    exit 1
else
    echo "Error (HTTP $http_code): $body"
    exit 1
fi
