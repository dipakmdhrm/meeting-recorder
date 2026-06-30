# Meeting Recorder

A meeting recorder that transcribes audio and generates structured notes using Google Gemini.

This repository is a monorepo with two independent apps — a Linux desktop applet and a native Android app — that share the same storage format so recordings are accessible from both.

| Path | Contents |
|---|---|
| `linux/` | GTK4 + libadwaita desktop applet (Debian / Ubuntu / Fedora / Arch) |
| `android/` | Native Android app (Kotlin + Jetpack Compose) |

---

## Linux App

### Features

- **Record** system audio + microphone simultaneously, or microphone only
- **Transcribe** with Google Gemini or local Whisper (timestamped, speaker-labeled transcript)
- **Summarize** into structured Markdown notes with Google Gemini or local Ollama
- **Summarize from the library** — re-run summarization for any past meeting from the meetings browser
- **Local models** — run fully offline with no API key required
- **Customizable prompts** — edit transcription and summarization prompts in Settings
- **System tray** integration — a StatusNotifierItem (SNI) exposed over D-Bus; left-click focuses the window where the host supports it, otherwise opens the menu
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

- Linux with a supported package manager: **apt** (Debian/Ubuntu/Mint), **dnf** (Fedora/RHEL), or **pacman** (Arch/Manjaro). Works on both **x86_64** and **arm64/aarch64**.
- System packages installed by `linux/install.sh`: `ffmpeg`, `pulseaudio-utils`, `pipewire-pulse`, Python 3 with GTK4 + libadwaita bindings

> **Look & theming:** the app uses **libadwaita**, so it follows your system **light/dark** preference and renders in the Adwaita style. On non-GNOME desktops (KDE, XFCE, Cinnamon, …) it still runs perfectly but keeps the Adwaita look rather than matching a custom desktop theme — this is libadwaita's intended behavior.
- Python packages (installed into a venv): see `linux/requirements.txt`

The base install is **Gemini-only and minimal** — no local engines or GPU libraries are installed by default. Each local option below is installed **on demand** from **Settings → Models** when you choose it.

| Service | Requirement |
|---|---|
| **Gemini** (transcription or summarization) | Free API key from [aistudio.google.com](https://aistudio.google.com) — no local install |
| **Whisper** (local transcription) | Engine (`faster-whisper`) installed on opt-in; model downloaded from HuggingFace (~500 MB – 3 GB); **NVIDIA GPU or CPU** |
| **whisper.cpp** (local transcription, GPU) | Engine built from source on opt-in with the detected backend — **AMD (ROCm/Vulkan), Apple (Metal), NVIDIA (CUDA), or CPU**; GGML model downloaded from HuggingFace |
| **Ollama** (local summarization) | [Ollama](https://ollama.com) installed and running (`ollama serve`); uses NVIDIA/AMD/Apple GPU automatically |

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

All packages set up a Python venv at `/opt/meeting-recorder/venv` on first install with **only the Gemini-ready essentials**. The local transcription engines (Whisper / whisper.cpp), Ollama, and GPU runtimes (CUDA / ROCm) are installed later, on demand, from **Settings → Models**. The `.deb` and apt repository are architecture-independent (`all`) and work on both amd64 and arm64.

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

> **GNOME users:** The tray is a StatusNotifierItem (SNI), and GNOME has no built-in SNI host, so the icon needs the AppIndicator/KStatusNotifierItem extension to appear. `install.sh` installs it automatically; if you installed via a native package, install it manually:
> ```bash
> # Debian/Ubuntu
> sudo apt install gnome-shell-extension-appindicator
> # Fedora
> sudo dnf install gnome-shell-extension-appindicator
> # Arch
> sudo pacman -S gnome-shell-extension-appindicator
> ```
> Then enable it in the GNOME Extensions app and log out/in.
>
> Whether **left-click focuses the window** or **opens the menu** is decided by the SNI host: hosts that deliver the `Activate` action (e.g. KDE Plasma) focus the window, while the GNOME extension typically opens the menu on any click. KStatusNotifierItem-capable panels on XFCE, MATE, Cinnamon, KDE, LXQt, … show the icon natively without an extension.

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
| **Whisper** | Runs locally via `faster-whisper` | Engine installed + model downloaded in Settings → Models; NVIDIA GPU or CPU |
| **whisper.cpp** | Runs locally via a from-source whisper.cpp build | Engine built + GGML model downloaded in Settings → Models; **GPU on AMD / Apple / NVIDIA / Vulkan**, or CPU |

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
   - *Whisper*: install the engine (first time), then select a model and click Download
   - *whisper.cpp*: pick an acceleration backend and build the engine (first time), then download a GGML model
   - *Ollama*: set host and click Download next to your preferred model
3. **Prompts tab** — optionally customize the transcription or summarization prompt

### Settings Reference

#### General tab

| Setting | Description |
|---|---|
| Transcription service | Gemini (cloud), Whisper (local), or whisper.cpp (local, GPU) |
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

The Whisper engine (`faster-whisper`) is **not in the base install**. When Whisper is selected and the engine is missing, the section shows an **Install Whisper engine** button; once installed, the model controls appear.

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

The `faster-whisper` engine accelerates on **NVIDIA (CUDA)** and otherwise runs on CPU. For **AMD or Apple GPU** acceleration, use the whisper.cpp engine below.

**whisper.cpp (GPU-accelerated)**

A local engine that supports a wider range of GPUs. The engine is **built from source on opt-in** (a build toolchain is installed automatically); until then the section shows a **Build whisper.cpp engine** button.

| Setting | Description |
|---|---|
| Acceleration backend | `auto` (detect), or force `cuda` / `rocm` / `vulkan` / `metal` / `cpu`. Used for both the build and at runtime. The detected backend is shown next to the selector. |
| Model | GGML model to use for local transcription |
| Model list | Download status and one-click download for each available GGML model |

Available whisper.cpp (GGML) models: `large-v3-turbo` (~1.6 GB), `large-v3` (~3 GB), `medium` (~1.5 GB), `small` (~470 MB).

**GPU Acceleration**

This section detects your GPU vendor and offers the matching runtime install: **CUDA** for NVIDIA, **ROCm** for AMD, built-in **Metal** for Apple Silicon, or a note that only CPU is available (in which case Gemini is recommended for speed).

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

Note: transcription prompts apply to Gemini only — the local Whisper and whisper.cpp engines do not use a prompt.

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

- **Record** microphone audio (AAC/M4A) at a configurable quality, captured in a foreground service that survives brief interruptions
- **Transcribe** with Google Gemini
- **Summarize** into structured Markdown notes with Google Gemini
- **Auto-title** — generates a meeting title from the notes when none is provided
- **Generate from the library** — generate the transcript & notes, or regenerate notes, for any meeting from its detail screen
- **Recover failed recordings** — if processing fails, the raw audio is kept in your library so you can generate the transcript & notes later
- **Use Existing Recording** — import an external audio file and transcribe/summarize it
- **Silenced-mic warning** — if the system mutes the mic mid-recording (e.g. an answered call), the audio is kept and you're warned instead of getting a silent transcript
- **Do Not Disturb while recording** (optional) — silence notifications during capture
- **Meetings browser** — browse and read past transcripts and notes; rename or delete meetings
- **Audio playback** — play back recordings directly in the meeting detail view
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
# Requires Android SDK (API 35) and JDK 17
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
├── src/meeting_recorder/  # GTK4 + libadwaita desktop app (Python)
├── tests/                 # Unit tests
├── packaging/             # .deb / .rpm / PKGBUILD / launcher scripts
├── install.sh / uninstall.sh
└── requirements.txt
android/
├── app/src/main/
│   ├── java/com/github/meetingrecorder/
│   │   ├── audio/         # MediaRecorder wrapper + foreground recording service
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
