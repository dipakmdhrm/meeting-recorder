#!/usr/bin/env bash
# uninstall.sh — Remove Meeting Recorder
set -euo pipefail

APP_NAME="meeting-recorder"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[info]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

# ── 1. Kill running instance ──────────────────────────────────────────────────
if pgrep -f "meeting_recorder" > /dev/null 2>&1; then
    info "Stopping running instance…"
    pkill -f "meeting_recorder" || true
    sleep 1
fi

# ── 2. Install directory (source + venv + app log) ───────────────────────────
if [ -d "$INSTALL_DIR" ]; then
    info "Removing $INSTALL_DIR…"
    rm -rf "$INSTALL_DIR"
fi

# ── 3. Launcher binary ───────────────────────────────────────────────────────
if [ -f "$BIN_DIR/$APP_NAME" ]; then
    info "Removing launcher $BIN_DIR/$APP_NAME…"
    rm -f "$BIN_DIR/$APP_NAME"
fi

# ── 4. Desktop entry ─────────────────────────────────────────────────────────
if [ -f "$APPS_DIR/$APP_NAME.desktop" ]; then
    info "Removing desktop entry…"
    rm -f "$APPS_DIR/$APP_NAME.desktop"
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
fi

# ── 5. Autostart entry ───────────────────────────────────────────────────────
if [ -f "$AUTOSTART_DIR/$APP_NAME.desktop" ]; then
    info "Removing autostart entry…"
    rm -f "$AUTOSTART_DIR/$APP_NAME.desktop"
fi

echo
warn "The following were NOT removed (your data):"
warn "  ~/.config/meeting-recorder/  (config + API keys)"
warn "  ~/meetings/                  (recordings, transcripts, notes)"
warn ""
warn "To remove config:  rm -rf ~/.config/meeting-recorder"

echo
info "Uninstall complete."
