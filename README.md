# Meeting Recorder

A Linux desktop applet that records meetings, transcribes them, and generates structured notes — all in a few clicks. Supports both cloud (Google Gemini) and local (Whisper + Ollama) processing.

## Features

- **Record** system audio + microphone simultaneously, or microphone only
- **Transcribe** with Google Gemini or local Whisper (timestamped, speaker-labeled transcript)
- **Summarize** into structured Markdown notes with Google Gemini or local Ollama
- **Local models** — run fully offline with no API key required
- **Customizable prompts** — edit transcription and summarization prompts in Settings
- **System tray** integration (AyatanaAppIndicator3 / pystray fallback)
- **Call detection** — optionally monitor for active calls and get notified to start recording
- **Start at system startup** — optionally launch automatically on login
- **Organized output** — files saved in a dated hierarchy under your chosen output folder

## Output Structure

Each recording session creates a folder:

```
~/meetings/
└── 2026/
    └── March/
        └── 04/
            └── 14-30_Standup/
                ├── recording.mp3
                ├── transcript.md
                └── notes.md
```

When using "Use Existing Recording", transcript and notes are saved next to the selected file.

## Requirements

- Linux with a supported package manager: **apt** (Debian/Ubuntu/Mint), **dnf** (Fedora/RHEL), or **pacman** (Arch/Manjaro)
- System packages installed by `install.sh`: `ffmpeg`, `pulseaudio-utils`, `pipewire-pulse`, Python 3 with GTK3 bindings
- Python packages (installed into a venv): see `requirements.txt`

Depending on which services you use:

| Service | Requirement |
|---|---|
| **Gemini** (transcription or summarization) | Free API key from [aistudio.google.com](https://aistudio.google.com) |
| **Whisper** (local transcription) | Model downloaded from HuggingFace (~500 MB – 3 GB); NVIDIA GPU optional |
| **Ollama** (local summarization) | [Ollama](https://ollama.com) installed and running (`ollama serve`) |

## Installation

### Option 1: native package (recommended)

Download the package for your distro from the [Releases](../../releases) page.

**Debian / Ubuntu / Mint (.deb)**
```bash
sudo dpkg -i meeting-recorder_*.deb
sudo apt-get install -f   # installs any missing dependencies
# To uninstall:
sudo apt remove meeting-recorder
```

**Fedora / RHEL / openSUSE (.rpm)**
```bash
sudo dnf install ./meeting-recorder-*.rpm
# To uninstall:
sudo dnf remove meeting-recorder
```

**Arch / Manjaro (.pkg.tar.zst)**
```bash
sudo pacman -U meeting-recorder-*.pkg.tar.zst
# To uninstall:
sudo pacman -R meeting-recorder
```

All packages set up a Python venv at `/opt/meeting-recorder/venv` on first install. Ollama and CUDA can be installed later from **Settings → Models**.

### Option 2: install.sh (from source)

```bash
git clone <repo-url>
cd meeting-recorder
./install.sh
```

`install.sh` detects your package manager (apt / dnf / pacman) and installs all system dependencies, then creates a Python venv with all required packages. Ollama and CUDA can be installed later from **Settings → Models**.

To uninstall:

```bash
./uninstall.sh
```

Then launch either way:

```bash
meeting-recorder
# or from your application menu: "Meeting Recorder"
```

> **GNOME users:** System tray requires the AppIndicator extension. `install.sh` installs it automatically; if you installed via a native package (.deb/.rpm/.pkg.tar.zst), install it manually:
> ```bash
> # Debian/Ubuntu
> sudo apt install gnome-shell-extension-appindicator
> # Fedora
> sudo dnf install gnome-shell-extension-appindicator
> # Arch
> sudo pacman -S gnome-shell-extension-appindicator
> ```
> Then enable it in the GNOME Extensions app and log out/in.

## Running from Source

```bash
cd meeting-recorder
python3 -m venv .venv --system-site-packages
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src python3 -m meeting_recorder
```

## Recording Modes

| Mode | What is captured | When to use |
|------|-----------------|-------------|
| **Record (Headphones)** | Microphone + system audio (calls, browser, etc.) | You're wearing headphones — no echo risk |
| **Record (Speaker)** | Microphone only | Laptop speakers — avoids loopback echo |

## Services

### Transcription

| Service | How it works | Requires |
|---|---|---|
| **Google Gemini** | Audio sent to Gemini API | API key |
| **Whisper** | Runs locally on your machine | Model downloaded in Settings → Models |

### Summarization

| Service | How it works | Requires |
|---|---|---|
| **Google Gemini** | Text sent to Gemini API | API key |
| **Ollama** | Runs locally via Ollama | Ollama running (`ollama serve`), model pulled in Settings → Models |

Mix and match freely — e.g. Whisper for transcription + Ollama for summarization runs fully offline with no API key.

## First-Time Setup

Open **Settings** (gear icon or tray menu):

1. **General tab** — choose your transcription and summarization services; set output folder and recording quality
2. **Models tab** — configure the selected services:
   - *Gemini*: paste your API key and choose a model
   - *Whisper*: select a model and click Download
   - *Ollama*: set host and click Download next to your preferred model
3. **Prompts tab** — optionally customize the transcription or summarization prompt

## Settings Reference

### General tab

| Setting | Description |
|---|---|
| Transcription service | Gemini (cloud) or Whisper (local) |
| Summarization service | Gemini (cloud) or Ollama (local) |
| Start at system startup | Launch automatically on login |
| Enable call detection | Monitor for active calls and notify you to start recording |
| Output folder | Where recordings and notes are saved (default: `~/meetings`) |
| Recording quality | Audio bitrate preset (Very High / High / Medium / Low) |

### Models tab

**Gemini**

| Setting | Description |
|---|---|
| API key | Required when Gemini is selected for transcription or summarization |
| Model | Gemini model to use (`gemini-flash-latest` recommended) |
| Processing timeout | Max time to wait for a Gemini response (1–10 min) |

**Whisper**

| Setting | Description |
|---|---|
| Whisper model | Model to use for local transcription |
| Model list | Download status and one-click download for each available model |

Available Whisper models:

| Model | Size | Notes |
|---|---|---|
| `large-v3-turbo` | ~1.6 GB | High quality, 8× faster than large-v3 — recommended |
| `distil-large-v3` | ~1.5 GB | Fast, near-large quality |
| `large-v3` | ~3 GB | Best accuracy, slow on CPU |
| `medium` | ~1.5 GB | Good balance |
| `small` | ~500 MB | Fast, lower accuracy |

GPU acceleration is used automatically if CUDA libraries are present. If they are not installed, a **Install CUDA Libraries** button appears in this section — it detects your package manager and runs the appropriate install command. Falls back to CPU otherwise.

**Ollama**

If Ollama is not installed, an **Install Ollama** button appears instead of the configuration UI. Once installed, the UI switches to the normal model management view automatically.

| Setting | Description |
|---|---|
| Ollama model | Model to use for local summarization |
| Ollama host | Ollama server address (default: `http://localhost:11434`) |
| Model list | Download status and one-click download for each available model |

Available Ollama models:

| Model | Size | Notes |
|---|---|---|
| `phi4-mini` | ~3 GB | Lightest, good quality |
| `gemma3:4b` | ~4 GB | Good quality |
| `qwen2.5:7b` | ~5 GB | Very capable |
| `llama3.1:8b` | ~5 GB | Very capable |
| `gemma3:12b` | ~8 GB | Best quality, high RAM required |

### Prompts tab

Customize the transcription and summarization prompts. Each has a **Reset to default** button. The `{transcript}` placeholder in the summarization prompt is replaced with the transcript text.

Note: transcription prompts apply to Gemini only — Whisper does not use a prompt.

## Workflow

1. Click **Record (Headphones)** or **Record (Speaker)** to start
2. The timer shows elapsed recording time; **Pause** / **Resume** as needed
3. Click **Stop** — a 5-second countdown begins (click **Cancel** to abort)
4. After 5 seconds, transcription starts automatically
5. When done, links to the transcript and notes files appear in the window

## Noise Reduction (Optional)

If your microphone picks up too much ambient noise, enable PipeWire's WebRTC noise suppression:

**Temporary (current session only):**
```bash
pactl load-module module-echo-cancel aec_method=webrtc noise_suppression=true
```

**Permanent:**

Create `~/.config/pipewire/pipewire-pulse.conf.d/echo-cancel.conf`:
```
pulse.cmd = [
  { cmd = "load-module" args = "module-echo-cancel aec_method=webrtc noise_suppression=true" flags = [] }
]
```

Then restart PipeWire:
```bash
systemctl --user restart pipewire pipewire-pulse
```

## Logs

Application logs:
```
~/.local/share/meeting-recorder/meeting-recorder.log
```

FFmpeg (recording) logs:
- **Native package or install.sh**: `/var/log/meeting-recorder/ffmpeg-<session-dir>.log`
- **Dev mode**: `ffmpeg.log` inside the recording directory

## Development

### Running tests

```bash
pip install pytest
pytest
```

Tests cover the service layer (`OllamaInstaller`, `CudaInstaller`, `OllamaClient`, `WhisperStatusChecker`) using dependency injection — no real shell commands or network calls are made. The cross-distro branch isolation tests in `tests/services/test_system_installer.py` are the key regression guard: they assert that changes to the apt-get path cannot silently affect the dnf or pacman path and vice-versa.

### CI

Every pull request to `main` runs:
- **Unit tests** on Python 3.10 and 3.12
- **Package build smoke tests** — builds `.deb` (ubuntu:24.04), `.rpm` (fedora:41), and `.pkg.tar.zst` (archlinux) in distro containers and verifies required paths are present in each artifact

## License

MIT
