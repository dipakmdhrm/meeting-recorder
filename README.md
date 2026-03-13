# Meeting Recorder

A Linux desktop applet that records meetings, transcribes them, and generates structured notes. Supports Debian/Ubuntu and Arch Linux, with both cloud and local processing options.

## Features

- **Record** system audio + microphone simultaneously, with separate tracks for better diarization
- **Transcribe** with Google Gemini, ElevenLabs Scribe v2, local Whisper, or 100+ providers via LiteLLM
- **Summarize** with Claude Code CLI (subscription), or any LLM via LiteLLM (Gemini, Ollama, OpenAI, Anthropic, etc.)
- **LiteLLM integration** — unified access to 100+ LLM providers with a single model string
- **Platform abstraction** — PulseAudio or PipeWire audio backends, gpu-screen-recorder for Wayland screen capture
- **API key store** — manage all API keys in Settings, injected as environment variables
- **Local models** — run fully offline with Whisper + Ollama (no API key required)
- **Customizable prompts** — edit transcription and summarization prompts in Settings
- **System tray** integration (AppIndicator / pystray fallback)
- **Call detection** — optionally monitor for active calls and get notified
- **Screen recording** — per-monitor Wayland-native recording via gpu-screen-recorder

## Supported Platforms

| Platform | Audio | Screen Recording | Install |
|----------|-------|-----------------|---------|
| Debian/Ubuntu | PulseAudio | Not supported | `install.sh` or `.deb` package |
| Arch Linux + KDE Plasma | PipeWire | gpu-screen-recorder | `install/install-arch.sh` |

## Transcription Providers

| Provider | Type | Model Selection |
|----------|------|----------------|
| **Google Gemini** | Cloud (direct) | Gemini model list in Settings |
| **ElevenLabs Scribe v2** | Cloud (direct) | Single model, native diarization |
| **Whisper** | Local (faster-whisper) | Model download in Settings |
| **LiteLLM** | Cloud (unified) | `provider/model` string (e.g. `groq/whisper-large-v3`) |

## Summarization Providers

| Provider | Type | Notes |
|----------|------|-------|
| **Claude Code CLI** | Local subprocess | Uses your Claude Code subscription, not API |
| **LiteLLM** | Cloud (unified) | Any model: `gemini/gemini-2.5-flash`, `ollama_chat/phi4-mini`, `openai/gpt-4o`, etc. |

## LiteLLM Model Strings

LiteLLM routes to providers via the model string prefix:

```
gemini/gemini-2.5-flash          # Google Gemini
ollama_chat/phi4-mini            # Local Ollama
openai/gpt-4o                    # OpenAI
anthropic/claude-sonnet-4-latest # Anthropic
openrouter/anthropic/claude-sonnet-4  # OpenRouter
groq/whisper-large-v3            # Groq (transcription)
```

Select from curated lists in Settings, or type any `provider/model` string.

## Installation

### Arch Linux

```bash
git clone <repo-url>
cd meeting-recorder
install/install-arch.sh
```

### Debian/Ubuntu

```bash
git clone <repo-url>
cd meeting-recorder
./install.sh
```

Or install the `.deb` package from the releases page.

## Configuration

All settings are managed via the Settings dialog (5 tabs):

- **General** — provider selection, LiteLLM model, output folder, quality, timeout
- **Platform** — audio backend, separate tracks, screen recording
- **Models** — Gemini model, Whisper model downloads, Ollama model downloads
- **API Keys** — environment variable key-value store for all provider API keys
- **Prompts** — custom transcription and summarization prompts

Config is stored at `~/.config/meeting-recorder/config.json` with `chmod 600`.

## Output Structure

Each recording session creates a folder:

```
~/meetings/
  2026-03-14_1430_standup/
    audio.mp3           # Combined audio
    mic.mp3             # Microphone track (if separate tracks enabled)
    system.mp3          # System audio track (if separate tracks enabled)
    transcript.md       # Timestamped, speaker-labeled transcript
    notes.md            # Structured meeting notes
```

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## Dependencies

- Python 3.11+, GTK3, ffmpeg
- `google-genai`, `pystray`, `Pillow`, `faster-whisper`, `litellm`, `elevenlabs`
- PipeWire (Arch) or PulseAudio (Debian) for audio capture
- Optional: `gpu-screen-recorder` for Wayland screen recording
- Optional: Ollama for local summarization
