#!/usr/bin/env bash
# Start LLTimmy agent
set -euo pipefail
cd "$(dirname "$0")"

echo "Starting LLTimmy..."
source .venv/bin/activate
python3 main.py
