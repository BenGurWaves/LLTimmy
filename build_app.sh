#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# build_app.sh — Build /Applications/LLTimmy.app
#
# Strategy:
#   1. Try PyInstaller first (single-directory .app bundle).
#   2. If PyInstaller fails (native extensions, etc.), fall back
#      to a lightweight shell-wrapper .app bundle that invokes
#      the venv Python directly.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_APP="$PROJECT_DIR/src/app.py"
APP_NAME="LLTimmy"
APP_DEST="/Applications/${APP_NAME}.app"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"
ICON_PATH="$PROJECT_DIR/icon.icns"

# Use system python if venv missing
if [ ! -f "$VENV_PYTHON" ]; then
    VENV_PYTHON="$(which python3)"
fi

echo "╔══════════════════════════════════════════════╗"
echo "║  Building ${APP_NAME}.app                    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Project:  $PROJECT_DIR"
echo "  Source:   $SRC_APP"
echo "  Python:   $VENV_PYTHON"
echo ""

# ── Remove old app ────────────────────────────────────────────
if [ -d "$APP_DEST" ]; then
    echo "[1/4] Removing old $APP_DEST ..."
    rm -rf "$APP_DEST"
else
    echo "[1/4] No existing app to remove."
fi

# ── Try PyInstaller ───────────────────────────────────────────
PYINSTALLER_OK=false

if "$VENV_PYTHON" -c "import PyInstaller" 2>/dev/null; then
    echo "[2/4] PyInstaller found — attempting build ..."
    cd "$PROJECT_DIR"

    "$VENV_PYTHON" -m PyInstaller \
        --name "$APP_NAME" \
        --windowed \
        --onedir \
        --noconfirm \
        --clean \
        --add-data "config.json:." \
        --add-data "agent_core.py:." \
        --add-data "memory_manager.py:." \
        --add-data "tools.py:." \
        --add-data "task_manager.py:." \
        --add-data "scheduler.py:." \
        --add-data "self_evolution.py:." \
        --hidden-import customtkinter \
        --hidden-import PIL \
        --hidden-import requests \
        --hidden-import chromadb \
        --hidden-import pysqlite3 \
        --collect-all customtkinter \
        ${ICON_PATH:+--icon "$ICON_PATH"} \
        "$SRC_APP" 2>&1 | tail -20 && PYINSTALLER_OK=true || true

    if $PYINSTALLER_OK && [ -d "$PROJECT_DIR/dist/${APP_NAME}.app" ]; then
        echo "[3/4] PyInstaller succeeded — installing ..."
        cp -R "$PROJECT_DIR/dist/${APP_NAME}.app" "$APP_DEST"
        echo "[4/4] Cleaning build artifacts ..."
        rm -rf "$PROJECT_DIR/build" "$PROJECT_DIR/dist" "$PROJECT_DIR/${APP_NAME}.spec"
        echo ""
        echo "  ✓  ${APP_NAME}.app installed to /Applications"
        exit 0
    else
        echo "  ⚠  PyInstaller build failed — falling back to shell wrapper."
    fi
else
    echo "[2/4] PyInstaller not installed — using shell wrapper."
fi

# ── Fallback: Shell-wrapper .app bundle ───────────────────────
echo "[3/4] Creating shell-wrapper .app bundle ..."

MACOS_DIR="$APP_DEST/Contents/MacOS"
RES_DIR="$APP_DEST/Contents/Resources"
mkdir -p "$MACOS_DIR" "$RES_DIR"

# Info.plist
cat > "$APP_DEST/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>LLTimmy</string>
    <key>CFBundleIdentifier</key>
    <string>com.bengur.lltimmy</string>
    <key>CFBundleName</key>
    <string>LLTimmy</string>
    <key>CFBundleDisplayName</key>
    <string>LLTimmy</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>14.0</string>
    <key>CFBundleShortVersionString</key>
    <string>14.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
    </dict>
</dict>
</plist>
PLIST

# Executable launcher script
cat > "$MACOS_DIR/$APP_NAME" << LAUNCHER
#!/usr/bin/env bash
# LLTimmy launcher — invokes venv Python with src/app.py
export PATH="/usr/local/bin:/opt/homebrew/bin:\$PATH"
cd "$PROJECT_DIR"
exec "$VENV_PYTHON" "$SRC_APP" "\$@"
LAUNCHER

chmod +x "$MACOS_DIR/$APP_NAME"

# Copy icon if available
if [ -f "$ICON_PATH" ]; then
    cp "$ICON_PATH" "$RES_DIR/icon.icns"
fi

echo "[4/4] Shell-wrapper .app bundle created."
echo ""
echo "  ✓  ${APP_NAME}.app installed to /Applications"
echo "     (Shell wrapper — runs from project venv)"
