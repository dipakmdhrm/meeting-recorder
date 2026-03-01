#!/usr/bin/env bash
# uninstall.sh — Remove Meeting Recorder
set -euo pipefail

APP_NAME="meeting-recorder"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[info]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

if [ -d "$INSTALL_DIR" ]; then
    info "Removing $INSTALL_DIR…"
    rm -rf "$INSTALL_DIR"
fi

if [ -f "$BIN_DIR/$APP_NAME" ]; then
    info "Removing launcher $BIN_DIR/$APP_NAME…"
    rm -f "$BIN_DIR/$APP_NAME"
fi

if [ -f "$APPS_DIR/$APP_NAME.desktop" ]; then
    info "Removing desktop entry…"
    rm -f "$APPS_DIR/$APP_NAME.desktop"
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
fi

warn "Config file ~/.config/meeting-recorder/config.json was NOT removed."
warn "To also remove your configuration and API keys, run:"
warn "  rm -rf ~/.config/meeting-recorder"

echo
info "Uninstall complete."
