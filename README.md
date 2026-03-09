# Meeting Recorder

A Linux desktop applet that records meetings, transcribes them with Google Gemini, and generates structured notes — all in a few clicks.

## Features

- **Record** system audio + microphone simultaneously, or microphone only
- **Transcribe** with Google Gemini (timestamped, speaker-labeled transcript)
- **Summarize** into structured Markdown notes with key points and action items
- **Customizable prompts** — edit transcription and summarization prompts in Settings
- **System tray** integration (AyatanaAppIndicator3 / pystray fallback)
- **Call detection** — optionally monitor for active calls and get notified to start recording
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

- Debian/Ubuntu-based Linux (tested on Ubuntu 22.04+)
- **Google Gemini API key** — get one free at [aistudio.google.com](https://aistudio.google.com)
- System packages installed by `install.sh`: `ffmpeg`, `pulseaudio-utils`, `pipewire-pulse`, Python 3 with GTK3 bindings
- Python packages (installed into a venv): `google-genai`, `pystray`, `Pillow`

## Installation

```bash
git clone <repo-url>
cd mint-meeting-recorder-applet
./install.sh
```

Then launch:

```bash
meeting-recorder
# or from your application menu: "Meeting Recorder"
```

> **GNOME users:** System tray requires the AppIndicator extension:
> ```bash
> sudo apt install gnome-shell-extension-appindicator
> gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
> ```

## Running from Source

```bash
cd mint-meeting-recorder-applet
python3 -m venv .venv --system-site-packages
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src python3 -m meeting_recorder
```

## Uninstall

```bash
./uninstall.sh
```

## Recording Modes

| Mode | What is captured | When to use |
|------|-----------------|-------------|
| **Record (Headphones)** | Microphone + system audio (calls, browser, etc.) | You're wearing headphones — no echo risk |
| **Record (Speaker)** | Microphone only | Laptop speakers — avoids loopback echo |

## First-Time Setup

Open **Settings** (gear icon or tray menu) and configure:

1. **API Keys tab** — paste your Gemini API key
2. **Services tab** — choose Gemini model (`gemini-2.5-flash` is the default)
3. **Output tab** — set where recordings are saved (default: `~/meetings`)
4. **Prompts tab** — optionally customize the transcription or summarization prompt
5. **Detection tab** — optionally enable automatic call detection

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

## Settings

| Tab | Setting | Description |
|-----|---------|-------------|
| Services | Transcription service | AI provider for transcription (currently: Gemini) |
| Services | Summarization service | AI provider for summarization (currently: Gemini) |
| Services | Gemini model | Model to use (`gemini-2.5-flash` recommended) |
| API Keys | Gemini API key | Required for all AI features |
| Output | Output folder | Where recordings and notes are saved |
| Detection | Enable call detection | Monitor for active calls and notify you |
| Prompts | Transcription prompt | Customize how Gemini transcribes audio |
| Prompts | Summarization prompt | Customize how Gemini formats meeting notes |

Prompts support a **Reset to default** button. The `{transcript}` placeholder in the summarization prompt is replaced with the transcript text.

## Workflow

1. Click **Record (Headphones)** or **Record (Speaker)** to start
2. The timer shows elapsed recording time; **Pause** / **Resume** as needed
3. Click **Stop** — a 5-second countdown begins (click **Cancel** to abort)
4. After 5 seconds, transcription starts automatically
5. When done, links to the transcript and notes files appear in the window

## Logs

Application logs:
```
~/.local/share/meeting-recorder/meeting-recorder.log
```

FFmpeg (recording) logs:
- **Installed**: `/var/log/meeting-recorder/ffmpeg-<session-dir>.log`
- **Dev Mode**: `ffmpeg.log` inside the recording directory

## License

MIT
