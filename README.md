# Meeting Recorder

A meeting recorder that transcribes audio and generates structured notes using Google Gemini.

This repository is a monorepo with two independent apps — a Linux desktop applet and a native Android app — that share the same storage format so recordings are accessible from both.

| Path | Contents |
|---|---|
| `linux/` | GTK3 desktop applet (Debian / Ubuntu / Fedora / Arch) |
| `android/` | Native Android app (Kotlin + Jetpack Compose) |

---

## Linux App

### Features

- **Record** system audio + microphone simultaneously, or microphone only
- **Transcribe** with Google Gemini or local Whisper (timestamped, speaker-labeled transcript)
- **Summarize** into structured Markdown notes with Google Gemini or local Ollama
- **Local models** — run fully offline with no API key required
- **Customizable prompts** — edit transcription and summarization prompts in Settings
- **System tray** integration (AyatanaAppIndicator3 / pystray fallback)
- **Call detection** — optionally monitor for active calls and get notified to start recording
- **Start at system startup** — optionally launch automatically on login

### Output Structure

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

### Requirements

- Linux with a supported package manager: **apt** (Debian/Ubuntu/Mint), **dnf** (Fedora/RHEL), or **pacman** (Arch/Manjaro)
- System packages installed by `linux/install.sh`: `ffmpeg`, `pulseaudio-utils`, `pipewire-pulse`, Python 3 with GTK3 bindings
- Python packages (installed into a venv): see `linux/requirements.txt`

Depending on which services you use:

| Service | Requirement |
|---|---|
| **Gemini** (transcription or summarization) | Free API key from [aistudio.google.com](https://aistudio.google.com) |
| **Whisper** (local transcription) | Model downloaded from HuggingFace (~500 MB – 3 GB); NVIDIA GPU optional |
| **Ollama** (local summarization) | [Ollama](https://ollama.com) installed and running (`ollama serve`) |

### Installation

#### Option 1: native package (recommended)

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

#### Option 2: install.sh (from source)

```bash
git clone <repo-url>
cd meeting-recorder
linux/install.sh
```

`linux/install.sh` detects your package manager (apt / dnf / pacman) and installs all system dependencies, then creates a Python venv with all required packages.

To uninstall:

```bash
linux/uninstall.sh
```

Then launch either way:

```bash
meeting-recorder
# or from your application menu: "Meeting Recorder"
```

> **GNOME users:** System tray requires the AppIndicator extension. `install.sh` installs it automatically; if you installed via a native package, install it manually:
> ```bash
> # Debian/Ubuntu
> sudo apt install gnome-shell-extension-appindicator
> # Fedora
> sudo dnf install gnome-shell-extension-appindicator
> # Arch
> sudo pacman -S gnome-shell-extension-appindicator
> ```
> Then enable it in the GNOME Extensions app and log out/in.

### Running from Source

```bash
cd meeting-recorder
python3 -m venv .venv --system-site-packages
.venv/bin/pip install -r linux/requirements.txt
PYTHONPATH=linux/src python3 -m meeting_recorder
```

### Recording Modes

| Mode | What is captured | When to use |
|------|-----------------|-------------|
| **Record (Headphones)** | Microphone + system audio (calls, browser, etc.) | You're wearing headphones — no echo risk |
| **Record (Speaker)** | Microphone only | Laptop speakers — avoids loopback echo |

### Services

#### Transcription

| Service | How it works | Requires |
|---|---|---|
| **Google Gemini** | Audio sent to Gemini API | API key |
| **Whisper** | Runs locally on your machine | Model downloaded in Settings → Models |

#### Summarization

| Service | How it works | Requires |
|---|---|---|
| **Google Gemini** | Text sent to Gemini API | API key |
| **Ollama** | Runs locally via Ollama | Ollama running (`ollama serve`), model pulled in Settings → Models |

Mix and match freely — e.g. Whisper for transcription + Ollama for summarization runs fully offline with no API key.

### First-Time Setup

Open **Settings** (gear icon or tray menu):

1. **General tab** — choose your transcription and summarization services; set output folder and recording quality
2. **Models tab** — configure the selected services:
   - *Gemini*: paste your API key and choose a model
   - *Whisper*: select a model and click Download
   - *Ollama*: set host and click Download next to your preferred model
3. **Prompts tab** — optionally customize the transcription or summarization prompt

### Settings Reference

#### General tab

| Setting | Description |
|---|---|
| Transcription service | Gemini (cloud) or Whisper (local) |
| Summarization service | Gemini (cloud) or Ollama (local) |
| Start at system startup | Launch automatically on login |
| Enable call detection | Monitor for active calls and notify you to start recording |
| Output folder | Where recordings and notes are saved (default: `~/meetings`) |
| Recording quality | Audio bitrate preset (Very High / High / Medium / Low) |

#### Models tab

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

GPU acceleration is used automatically if CUDA libraries are present. Falls back to CPU otherwise.

**Ollama**

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

#### Prompts tab

Customize the transcription and summarization prompts. Each has a **Reset to default** button. The `{transcript}` placeholder in the summarization prompt is replaced with the transcript text.

Note: transcription prompts apply to Gemini only — Whisper does not use a prompt.

### Workflow

1. Click **Record (Headphones)** or **Record (Speaker)** to start
2. The timer shows elapsed recording time; **Pause** / **Resume** as needed
3. Click **Stop** — a 5-second countdown begins (click **Cancel** to abort)
4. After 5 seconds, transcription starts automatically
5. When done, links to the transcript and notes files appear in the window

### Noise Reduction (Optional)

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

### Logs

Application logs written to `/var/log/meeting-recorder/` (fallback: `~/.local/share/meeting-recorder/`):

```
app.log    — DEBUG and INFO messages
error.log  — WARNING and above
```

---

## Android App

### Features

- **Record** microphone audio (AAC/M4A)
- **Transcribe** with Google Gemini
- **Summarize** into structured Markdown notes with Google Gemini
- **Auto-title** — generates a meeting title from the notes when none is provided
- **Meetings browser** — browse, search, and read past transcripts and notes
- Recordings saved to `Documents/Meetings/` — same structure as the Linux app

### Requirements

- Android 12+ (API 31)
- Google Gemini API key — free from [aistudio.google.com](https://aistudio.google.com)
- "All files access" (`MANAGE_EXTERNAL_STORAGE`) permission — required to read/write `Documents/Meetings/`

### Installation

Download `meeting-recorder-android-*.apk` from the [Releases](../../releases) page, transfer it to your phone, and install it (enable **Install from unknown sources** in Settings if prompted).

### Output Structure

Recordings are saved to `Documents/Meetings/` on external storage, in the same dated hierarchy as the Linux app:

```
Documents/Meetings/
└── 2026/
    └── March/
        └── 04/
            └── 14-30_Standup/
                ├── recording.m4a
                ├── transcript.md
                └── notes.md
```

### First-Time Setup

1. Open the app and tap the **Settings** icon
2. Paste your Gemini API key and choose a model (`gemini-flash-latest` recommended)
3. Return to the main screen — grant **All files access** when prompted
4. Tap the microphone button to start recording

### Building from Source

```bash
# Requires Android SDK (API 36) and JDK 17
cd android
./gradlew assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk

# Release build (requires signing credentials)
set -x KEYSTORE_PASSWORD your_store_pass
set -x KEY_ALIAS meetingrecorder
set -x KEY_PASSWORD your_key_pass
./gradlew assembleRelease
# APK: app/build/outputs/apk/release/app-release.apk
```

---

## Development

### Repository layout

```
linux/
├── src/meeting_recorder/  # GTK3 desktop app (Python)
├── tests/                 # Unit tests
├── packaging/             # .deb / .rpm / PKGBUILD / launcher scripts
├── install.sh / uninstall.sh
└── requirements.txt
android/
├── app/src/main/
│   ├── java/com/github/meetingrecorder/
│   │   ├── audio/         # MediaRecorder wrapper
│   │   ├── data/          # Config, Meeting, MeetingRepository, GeminiClient
│   │   └── ui/            # Compose screens + ViewModels
│   └── res/
├── app/src/test/          # Unit tests (no device required)
└── build.gradle.kts / settings.gradle.kts
```

### Running Linux tests

```bash
pip install pytest
pytest
```

### Running Android unit tests

```bash
cd android && ./gradlew test
```

### CI

Every pull request to `main` runs:

- **Unit tests** — Python 3.10 and 3.12
- **Package build smoke tests** — builds `.deb` (ubuntu:24.04), `.rpm` (fedora:41), and `.pkg.tar.zst` (archlinux) in distro containers
- **Android debug build** — compiles the Kotlin app with `./gradlew assembleDebug`

Pushing a tag triggers the release workflows:

| Tag pattern | Workflow | Output |
|---|---|---|
| `v*` (e.g. `v1.2.0`) | `release.yml` | `.deb`, `.rpm`, `.pkg.tar.zst` attached to GitHub Release; apt repo on `gh-pages` updated |
| `android-*` (e.g. `android-1.0.0`) | `release-android.yml` | Signed `.apk` attached to GitHub Release |

## License

MIT
