#!/bin/bash
# Run all tests for Drive Eraser project

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TESTS_DIR="$PROJECT_ROOT/tests"
VENV_DIR="/opt/drive-eraser/venv"

echo "Running Drive Eraser test suite..."
echo ""

# Check if venv exists
if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Error: Production venv not found at $VENV_DIR. Please run the install script first."
    exit 1
fi

# Run pytest with coverage using venv
cd "$PROJECT_ROOT"
"$VENV_DIR/bin/pytest" "$TESTS_DIR" -v --cov=backend --cov-report=term-missing --cov-report=html

echo ""
echo "Test suite completed."
echo "Coverage report generated in htmlcov/ directory."
