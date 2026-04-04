#!/usr/bin/env bash
# install.sh — Install Meeting Recorder on Debian, Fedora, or Arch-based Linux
set -euo pipefail

APP_NAME="meeting-recorder"
INSTALL_DIR="$HOME/.local/share/$APP_NAME"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
LAUNCHER="$BIN_DIR/$APP_NAME"
DESKTOP="$APPS_DIR/$APP_NAME.desktop"
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
    sudo apt-get install -y python3 python3-venv python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 libayatana-appindicator3-1 gir1.2-notify-0.7 libnotify4 libnotify-bin ffmpeg pulseaudio-utils pipewire-pulse 2>/dev/null || true
    
    info "Installing CUDA runtime libraries (apt)..."
    sudo apt-get install -y libcublas12 libcudart12 || 
        warn "Could not install CUDA libs — Whisper will fall back to CPU transcription."
}

install_deps_dnf() {
    info "Installing system dependencies (dnf)..."
    sudo dnf install -y python3 python3-devel python3-gobject gtk3 libayatana-appindicator-gtk3 libnotify pulseaudio-utils pipewire-pulseaudio ffmpeg
    
    info "Installing CUDA runtime libraries (dnf)..."
    if ! sudo dnf config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/fedora$(rpm -E %fedora)/x86_64/cuda-fedora$(rpm -E %fedora).repo; then
        warn "Could not add NVIDIA CUDA repository."
    fi
    sudo dnf install -y libcublas-12-x cuda-cudart-12-x || 
        warn "Could not install CUDA libs — Whisper will fall back to CPU transcription."
}

install_deps_pacman() {
    info "Installing system dependencies (pacman)..."
    sudo pacman -Syu --noconfirm python python-gobject gtk3 libayatana-appindicator libnotify libpulse pipewire-pulse ffmpeg
        
    info "Installing CUDA runtime libraries (pacman)..."
    sudo pacman -Syu --noconfirm cuda || 
        warn "Could not install CUDA libs — Whisper will fall back to CPU transcription."
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

# ── 3. GNOME appindicator warning ───────────────────────────────────────────
if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]]; then
    warn "GNOME detected. For system tray support, you may need to install the AppIndicator extension."
fi

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
cp -r "$SCRIPT_DIR/src" "$INSTALL_DIR/"

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
export PYTHONPATH="$INSTALL_DIR/src"
export MEETING_RECORDER_INSTALLED=1
exec "$VENV_DIR/bin/python" -m meeting_recorder "\$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
info "Launcher created at $LAUNCHER"

# ── 9. Desktop entry ─────────────────────────────────────────────────────────
mkdir -p "$APPS_DIR"
sed "s|LAUNCHER_PATH|$LAUNCHER|g" "$SCRIPT_DIR/meeting-recorder.desktop.template" 
    > "$DESKTOP"
chmod +x "$DESKTOP"
info "Desktop entry created at $DESKTOP"

# Update desktop database if available
update-desktop-database "$APPS_DIR" 2>/dev/null || true

# ── 10. Add ~/.local/bin to PATH hint ────────────────────────────────────────
if ! echo "$PATH" | grep -q "$BIN_DIR"; then
    warn "$BIN_DIR is not in your PATH."
    warn "Add it to your shell profile: export PATH="\$HOME/.local/bin:\$PATH""
fi

echo
info "Installation complete!"
info "Run:  $APP_NAME"
info "Or launch from your application menu: Meeting Recorder"
