#!/usr/bin/env bash
# Start LLTimmy Doctor watchdog
set -euo pipefail
cd "$(dirname "$0")"

echo "Starting LLTimmy Doctor..."
source .venv/bin/activate
python3 doctor.py
