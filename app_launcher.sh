#!/bin/bash
# LLTimmy Native Desktop App Launcher
# - Doctor runs as background daemon (persists even if app closes)
# - Timmy native app runs in foreground (CustomTkinter desktop UI)
# - No browser, no Gradio, no localhost needed

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$BASE_DIR/.venv/bin/python3"
TIMMY_PID_FILE="/tmp/timmy.pid"
DOCTOR_PID_FILE="/tmp/doctor.pid"

# Ensure Doctor is running in background
start_doctor() {
    if [ -f "$DOCTOR_PID_FILE" ]; then
        DPID=$(cat "$DOCTOR_PID_FILE" 2>/dev/null)
        if kill -0 "$DPID" 2>/dev/null; then
            return  # Already running
        fi
    fi
    cd "$BASE_DIR"
    "$VENV_PYTHON" doctor.py > /tmp/doctor.log 2>&1 &
    echo $! > "$DOCTOR_PID_FILE"
    echo "Doctor daemon started (PID $!)"
}

# Stop any old Timmy processes
stop_old_timmy() {
    if [ -f "$TIMMY_PID_FILE" ]; then
        TPID=$(cat "$TIMMY_PID_FILE" 2>/dev/null)
        if kill -0 "$TPID" 2>/dev/null; then
            kill "$TPID" 2>/dev/null
            sleep 1
            kill -9 "$TPID" 2>/dev/null
        fi
        rm -f "$TIMMY_PID_FILE"
    fi
}

# Cleanup on exit (Timmy PID cleaned up by app itself, Doctor keeps running)
cleanup() {
    rm -f "$TIMMY_PID_FILE" 2>/dev/null
    exit 0
}
trap cleanup EXIT INT TERM

# Start Doctor daemon
start_doctor

# Kill any lingering old Timmy processes
stop_old_timmy

# Launch Timmy native desktop app (foreground, blocks until window closed)
echo "Starting LLTimmy native desktop app..."
cd "$BASE_DIR"
"$VENV_PYTHON" timmy_app.py 2>/tmp/timmy.log

echo "LLTimmy closed. Doctor continues running in background."
