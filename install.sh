#!/usr/bin/env bash
# install.sh — Install Meeting Recorder on Debian-based Linux
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
info "Installing system dependencies (requires sudo)…"
sudo apt-get update -qq
sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    libayatana-appindicator3-1 \
    gir1.2-notify-0.7 \
    libnotify4 \
    libnotify-bin \
    ffmpeg \
    pulseaudio-utils \
    pipewire-pulse 2>/dev/null || true

# CUDA runtime libs for GPU-accelerated Whisper transcription (NVIDIA only, safe to skip)
info "Installing CUDA runtime libraries (required for GPU Whisper transcription)…"
sudo apt-get install -y libcublas12 libcudart12 || \
    warn "Could not install CUDA libs — Whisper will fall back to CPU transcription."

# ── 2. Ollama ────────────────────────────────────────────────────────────────
if command -v ollama &>/dev/null; then
    info "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
else
    info "Installing Ollama…"
    curl -fsSL https://ollama.com/install.sh | sh
fi

# ── 3. GNOME appindicator warning ───────────────────────────────────────────
if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]]; then
    warn "GNOME detected. For system tray support, install the AppIndicator extension:"
    warn "  sudo apt install gnome-shell-extension-appindicator"
    warn "  Then enable it via GNOME Extensions app or:"
    warn "  gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com"
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
sed -e "s|LAUNCHER_PATH|$LAUNCHER|g" \
    -e "s|ICON_PATH|$INSTALL_DIR/src/meeting_recorder/assets/icons/meeting-recorder.svg|g" \
    "$SCRIPT_DIR/meeting-recorder.desktop.template" > "$DESKTOP"
chmod +x "$DESKTOP"
info "Desktop entry created at $DESKTOP"

# Update desktop database if available
update-desktop-database "$APPS_DIR" 2>/dev/null || true

# ── 10. Add ~/.local/bin to PATH hint ────────────────────────────────────────
if ! echo "$PATH" | grep -q "$BIN_DIR"; then
    warn "$BIN_DIR is not in your PATH."
    warn "Add it to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo
info "Installation complete!"
info "Run:  $APP_NAME"
info "Or launch from your application menu: Meeting Recorder"
