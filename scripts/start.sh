#!/bin/bash
set -e

cd /opt/drive-eraser
exec /opt/drive-eraser/venv/bin/python backend/app.py
