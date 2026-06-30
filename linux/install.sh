#!/usr/bin/env bash
# install.sh — Install Meeting Recorder on Debian, Fedora, or Arch-based Linux.
set -euo pipefail

APP_NAME="meeting-recorder"
# The desktop file is named after the GTK application id so the GNOME/Wayland
# shell (and Dash to Panel) can map a running window back to it and show the app
# icon instead of a generic one.
APP_ID="io.github.dipakmdhrm.MeetingRecorder"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
LAUNCHER="$BIN_DIR/$APP_NAME"
DESKTOP="$APPS_DIR/$APP_ID.desktop"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()    { echo -e "${GREEN}[info]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC} $*"; }
err()     { echo -e "${RED}[error]${NC} $*" >&2; }

# ── 1. System dependencies ──────────────────────────────────────────────────
install_deps_apt() {
    info "Installing system dependencies (apt)..."
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 gir1.2-notify-0.7 libnotify4 libnotify-bin ffmpeg pulseaudio-utils pipewire-pulse 2>/dev/null || true

}

install_deps_dnf() {
    info "Installing system dependencies (dnf)..."
    sudo dnf install -y python3 python3-devel python3-gobject gtk4 libadwaita libnotify pulseaudio-utils pipewire-pulseaudio ffmpeg

}

install_deps_pacman() {
    info "Installing system dependencies (pacman)..."
    sudo pacman -Syu --noconfirm python python-gobject gtk4 libadwaita libnotify libpulse pipewire-pulse ffmpeg

}

if command -v apt-get &>/dev/null; then
    install_deps_apt
elif command -v dnf &>/dev/null; then
    install_deps_dnf
elif command -v pacman &>/dev/null; then
    install_deps_pacman
else
    err "Unsupported package manager. Please install dependencies manually."
    exit 1
fi

# The app exposes its tray as a StatusNotifierItem (SNI) over D-Bus. GNOME has no
# built-in SNI host, so the AppIndicator/KStatusNotifierItem extension is needed
# to make the tray icon appear (it provides the SNI host, not the old library).
install_gnome_extensions() {
    if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]]; then
        info "GNOME detected. Installing AppIndicator/KStatusNotifierItem extension (SNI host)..."
        if command -v apt-get &>/dev/null; then
            sudo apt-get install -y gnome-shell-extension-appindicator
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y gnome-shell-extension-appindicator
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm gnome-shell-extension-appindicator
        fi
        warn "Please enable the 'AppIndicator and KStatusNotifierItem Support' extension in the GNOME Extensions app, and then log out and log back in."
    fi
}

install_gnome_extensions

# ── 4. Virtual environment ───────────────────────────────────────────────────
info "Creating virtual environment at $VENV_DIR…"
mkdir -p "$INSTALL_DIR"
python3 -m venv "$VENV_DIR" --system-site-packages

# ── 5. Python dependencies ───────────────────────────────────────────────────
info "Installing Python dependencies…"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

# ── 6. Copy source ───────────────────────────────────────────────────────────
info "Copying application source…"
mkdir -p "$INSTALL_DIR/linux"
cp -r "$SCRIPT_DIR/src" "$INSTALL_DIR/linux/"

# ── 7. System log directory ──────────────────────────────────────────────────
SYSTEM_LOG_DIR="/var/log/meeting-recorder"
info "Creating system log directory at $SYSTEM_LOG_DIR…"
sudo mkdir -p "$SYSTEM_LOG_DIR"
sudo chown "$USER:$USER" "$SYSTEM_LOG_DIR"
sudo chmod 755 "$SYSTEM_LOG_DIR"

# ── 8. Launcher script ───────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" << LAUNCHER_EOF
#!/usr/bin/env bash
export PYTHONPATH="$INSTALL_DIR/linux/src"
export MEETING_RECORDER_INSTALLED=1
exec "$VENV_DIR/bin/python" -m meeting_recorder "\$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
info "Launcher created at $LAUNCHER"

# ── 9. Desktop entry ─────────────────────────────────────────────────────────
mkdir -p "$APPS_DIR"
# Remove legacy (pre-rename) entries so the app isn't listed twice.
rm -f "$APPS_DIR/$APP_NAME.desktop" "$APPS_DIR/com.github.mint-meeting-recorder.desktop"
sed "s|LAUNCHER_PATH|$LAUNCHER|g" "$SCRIPT_DIR/meeting-recorder.desktop.template" \
    > "$DESKTOP"
chmod +x "$DESKTOP"
info "Desktop entry created at $DESKTOP"

# Update desktop database if available
update-desktop-database "$APPS_DIR" 2>/dev/null || true

# ── 9b. Application icon (hicolor theme) ─────────────────────────────────────
# The desktop file's Icon=meeting-recorder key is what the shell uses to render
# the window/launcher icon, so we install under that single themed name.
ICONS_SRC="$SCRIPT_DIR/src/meeting_recorder/assets/icons/hicolor"
ICON_THEME_DIR="$HOME/.local/share/icons/hicolor"
info "Installing application icons…"
for size in 16 24 32 48 64 128 256; do
    dest_dir="$ICON_THEME_DIR/${size}x${size}/apps"
    mkdir -p "$dest_dir"
    install -m 644 "$ICONS_SRC/${size}x${size}/apps/meeting-recorder.png" \
        "$dest_dir/meeting-recorder.png"
done
mkdir -p "$ICON_THEME_DIR/scalable/apps"
install -m 644 "$ICONS_SRC/scalable/apps/meeting-recorder.svg" \
    "$ICON_THEME_DIR/scalable/apps/meeting-recorder.svg"
# Clean up the icon installed under the previous (malformed) app id, if present.
rm -f "$ICON_THEME_DIR"/*/apps/com.github.mint-meeting-recorder.png \
      "$ICON_THEME_DIR/scalable/apps/com.github.mint-meeting-recorder.svg"
gtk-update-icon-cache -f -t "$ICON_THEME_DIR" 2>/dev/null || true

# ── 10. Add ~/.local/bin to PATH hint ────────────────────────────────────────
if ! echo "$PATH" | grep -q "$BIN_DIR"; then
    warn "$BIN_DIR is not in your PATH."
    warn "Add it to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo
info "Installation complete!"
info "Run:  $APP_NAME"
info "Or launch from your application menu: Meeting Recorder"
