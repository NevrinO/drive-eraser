#!/bin/bash
# Install test dependencies for Drive Eraser project
# This script installs pytest and test-related packages separately from production dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="/opt/drive-eraser/venv"

echo "Installing test dependencies..."

# Use production venv
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/pip" ]; then
    echo "Using production venv at $VENV_DIR"
    PIP="$VENV_DIR/bin/pip"
else
    echo "Error: Production venv not found at $VENV_DIR. Please run the install script first."
    exit 1
fi

# Install test dependencies
"$PIP" install -r "$PROJECT_ROOT/requirements-test.txt"

echo "Test dependencies installed successfully."
echo "Run tests with: ./scripts/tests-run.sh"
