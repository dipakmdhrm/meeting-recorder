# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git workflow — IMPORTANT

**Never push directly to `main`.** Always work on a feature branch and open a pull request so the GitHub Actions CI pipeline can run tests before merging.

1. Create a branch from the latest `main`:
   ```bash
   git checkout main && git pull
   git checkout -b <descriptive-branch-name>
   ```
2. Commit changes on the branch.
3. Push the branch and open a PR targeting `main`:
   ```bash
   git push -u origin <descriptive-branch-name>
   gh pr create --base main --title "..." --body "..."
   ```
4. Wait for CI to pass before merging.
5. After the PR is merged, tag releases from `main` (never from a feature branch).

This applies to all agents (Claude, Gemini, etc.) — no direct pushes to `main` under any circumstances.

---

## What this repo is

A monorepo with two independent apps that share the same on-disk recording format (`YYYY/MonthName/DD/HH-MM[_title]/recording.m4a|mp3 + transcript.md + notes.md`):

- `linux/` — GTK3 desktop applet (Python), runs on Debian/Ubuntu/Fedora/Arch
- `android/` — Kotlin/Jetpack Compose app (minSdk 31)

---

## Commands

### Linux app

```bash
# Run
PYTHONPATH=linux/src python3 -m meeting_recorder

# All tests
pytest

# Single test file
pytest linux/tests/services/test_whisper_service.py

# Single test
pytest linux/tests/services/test_whisper_service.py::ClassName::test_name
```

`pyproject.toml` sets `testpaths = ["linux/tests"]` and `pythonpath = ["linux/src"]`, so `pytest` works from the repo root.

### Android app

```bash
cd android

# Build
./gradlew assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk

# All unit tests
./gradlew test

# Single test class
./gradlew test --tests "com.github.meetingrecorder.MeetingRepositoryTest"

# Install to connected emulator / device
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

---

## Linux architecture

**Entry:** `__main__.py` → `app.py` starts the GLib main loop and wires the tray icon, main window, and call detector together.

**Audio recording** (`audio/`):
- `recorder.py` spawns `parec` (raw s16le PCM) into named pipes, then `ffmpeg amix` to mix mic + system audio into MP3. `parec` is started before `ffmpeg` to avoid a deadlock on pipe open.
- Pause/resume uses `SIGSTOP`/`SIGCONT` on the `parec` processes.
- Two modes: mic+system (`Record (Headphones)`) and mic-only (`Record (Speaker)`).

**AI processing** (`processing/`):
- `Pipeline` runs transcription then summarization as separate calls (a single dual-prompt call was removed because the model would cut transcription short to save output budget for notes).
- `transcription.py` / `summarization.py` expose factory functions (`create_transcription_provider`, `create_summarization_provider`) that return a provider based on config.
- Providers: `providers/gemini.py`, `providers/whisper.py`, `providers/ollama.py`. Each implements `.transcribe()` or `.summarize()` and optionally `.unload()` to free GPU VRAM.
- Before running Whisper, the pipeline evicts any loaded Ollama models from VRAM.

**Config:** `~/.config/meeting-recorder/config.json`, `chmod 600`. Empty string for any prompt key = use built-in default (defined in `config/defaults.py`).

**UI** (`ui/`): `main_window.py` (recording controls), `settings_dialog.py` (tabbed settings), `meeting_explorer.py` (past meetings browser), `tray.py` (AyatanaAppIndicator3 with pystray fallback).

**Import convention:** Provider files use 3-dot relative imports (`from ...config.defaults import …`). Files outside `meeting_recorder/` use absolute imports (`from meeting_recorder.config.defaults import …`).

---

## Android architecture

**Application class:** `MeetingRecorderApp` initialises two singletons on startup: `Config` (SharedPreferences wrapper) and `MeetingRepository` (file-system meeting store rooted at `Documents/Meetings/`).

**Navigation:** `AppNavGraph` (Compose Navigation) with four routes:
- `main` → `MainScreen` (record button, status)
- `settings` → `SettingsScreen` (General tab + Prompts tab)
- `meetings` → `MeetingsScreen` (list of past meetings)
- `meeting_detail/{meetingPath}` → `MeetingDetailScreen` (Notes / Transcript / Audio tabs)

File system paths are passed as nav arguments with `/` encoded as `%2F`.

**Settings save model:** Each settings tab holds local draft state in the Composable. The ViewModel setters write directly to `Config`/SharedPreferences. The Save button is what calls the setters — nothing is persisted on keystroke. Empty string stored for a prompt = use built-in default (same convention as Linux).

**Gemini API** (`data/GeminiClient.kt`): Manual OkHttp implementation (no Gemini SDK). Flow: resumable upload init → upload bytes → poll `GET /v1beta/files/{id}` until `state == ACTIVE` → `generateContent`. The poll response is a flat JSON object (not wrapped in a `"file"` key).

**Audio:** `MediaRecorder` → MPEG_4/AAC 128 kbps → `.m4a`. Playback uses `MediaPlayer` in `MeetingDetailViewModel`; the Audio tab is only shown when `hasAudio` is true (i.e. a recording file was found on disk).

**Storage:** `Documents/Meetings/YYYY/MonthName/DD/HH-MM[_title]/` on external storage (`MANAGE_EXTERNAL_STORAGE` permission required).

---

## Test coverage boundaries

### Linux
Tests in `linux/tests/services/` cover `OllamaService`, `WhisperService`, and `SystemInstaller` with mocks/temp dirs.

### Android
JVM-only unit tests (no Robolectric) in `app/src/test/`:
- `ConfigTest` — validates constants and model list invariants
- `MeetingRepositoryTest` — full coverage of listing, parsing, creating, and saving meetings using `TemporaryFolder`
- `GeminiClientTest` — full coverage of the upload→poll→generate flow using `MockWebServer`

ViewModels (`MeetingDetailViewModel`, `SettingsViewModel`) and all Compose UI are **not** unit-tested; they require Android platform APIs or the Compose testing framework, neither of which is in the current test setup.
