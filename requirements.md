## Use Case
- I take meetings and calls on my PC
- I want to record the audio of the calls (both microphone input and system audio output)
- I want to transcribe the call and store the transcript in markdown format
- I want to summarize the call and store the meeting notes in markdown format

---

## App Behaviour

### Main Window
- When the app opens, it shows a **Record** button and a recording timer (at 00:00)
- When the user presses **Record**:
  - Recording starts (mic input + system audio output via loopback)
  - The Record button is replaced by **Pause** and **Stop** buttons
  - The timer starts counting up
- When the user presses **Pause**:
  - Recording is paused
  - The timer stops
  - The Pause button is replaced by a **Resume** button
- When the user presses **Resume**:
  - Recording resumes
  - The timer resumes
  - The Resume button is replaced by the Pause button
- When the user presses **Stop**:
  - Recording stops and the audio file is saved
  - The UI shows a processing state with a status message (e.g. "Transcribing...", "Summarizing...")
  - Transcription runs, followed by summarization
  - On completion: show the paths to the output files with an option to open the output folder
  - On error: show an error message with a Retry option

### System Tray
- The app provides a system tray icon with a context menu:
  - **Start Recording**
  - **Pause Recording** (shown when recording is active)
  - **Resume Recording** (shown when recording is paused)
  - **Stop Recording** (shown when recording is active or paused)
- Stopping via the tray automatically triggers transcription and summarization
- A system notification is shown when processing is complete, including the output file paths
- Note: on GNOME desktops, the `gnome-shell-extension-appindicator` extension must be installed for the tray icon to appear

---

## Output & File Management

- Output files are saved to a user-configured output folder (default: `~/meetings/`)
- Each meeting produces three files, named by timestamp and optional meeting title:
  - Audio recording: `YYYY-MM-DD_HH-MM_<title>.mp3`
  - Transcript: `YYYY-MM-DD_HH-MM_<title>_transcript.md`
  - Meeting notes: `YYYY-MM-DD_HH-MM_<title>_notes.md`
- The user can optionally enter a meeting title before or after recording; if omitted the title is excluded from the filename
- The audio file is kept after processing (not deleted automatically)

---

## Technical Specification

### Transcription
The user can choose their preferred transcription service:
- **Google Gemini** — native audio input; handles transcription and summarization in a single API call
- **OpenAI Whisper** — dedicated STT API; best-in-class accuracy

### Summarization
The user can choose their preferred summarization service independently of transcription:
- **Google Gemini** — can be paired with Gemini transcription for a single API call and key
- **OpenAI GPT-4o** — high quality summarization
- **Anthropic Claude** — alternative LLM option (note: Anthropic does not support audio, so it cannot be used for transcription)

### Service Configuration Notes
- If Gemini is selected for both transcription and summarization, a single API call handles both (most efficient)
- If different providers are selected for each, two separate API calls are made after recording stops
- Only API keys for the selected services need to be configured
- The app validates the selected combination and shows a warning if an invalid pairing is chosen (e.g. Anthropic selected for transcription)

### Call Detection (Opt-in)
- The app can optionally monitor for active calls and notify the user to start recording
- When enabled, the app runs a background monitor that detects calls using two complementary methods:
  1. **Process watching**: polls running processes every few seconds for known call applications (Zoom, Teams, Discord, Slack, Skype, WebEx)
  2. **Audio stream monitoring**: watches PipeWire/PulseAudio for any new microphone capture stream, catching browser-based calls (Google Meet, Teams web, etc.) that process watching would miss
- When a call is detected, a system notification is sent: "A call may have started. Click to start recording."
- Clicking the notification opens the app (or brings it to focus if already open)
- This feature is disabled by default and must be opted into via Settings
- Known limitation: may produce false positives if other apps use the microphone (e.g. voice search, games)

### Settings UI
The app provides a settings screen to configure:
- Transcription service: Gemini | OpenAI Whisper
- Summarization service: Gemini | OpenAI GPT-4o | Anthropic Claude
- API keys (only for selected services):
  - Google Gemini API key
  - OpenAI API key
  - Anthropic API key
- Preferred model per service (e.g. gemini-1.5-flash, gpt-4o, claude-sonnet-4-6)
- Output folder path
- Call detection: enable/disable toggle

### API Key Storage
- API keys are stored in a local config file (`~/.config/meeting-recorder/config.json`)
- The config file should have user-only read permissions (chmod 600)

### Error Handling
- If an API call fails: show a clear error message and offer a Retry option
- If no audio input/output devices are found: show an error at startup
- If API keys are not configured: prompt the user to open Settings before recording

### Platform
- Standalone desktop application targeting Debian-based Linux distributions
- Built with Python and GTK (PyGObject) for the UI
- Audio capture via PipeWire/PulseAudio with a loopback sink for system audio

### Installation
The app is installed via a shell script (`install.sh`):
1. Installs system dependencies via `apt` (python3, python3-venv, GTK libs, PipeWire/PulseAudio libs, libappindicator3)
2. Creates a virtual environment at `~/.local/share/meeting-recorder/venv`
3. Installs Python dependencies into the venv
4. Creates a launcher script at `~/.local/bin/meeting-recorder`
5. Creates a `.desktop` entry at `~/.local/share/applications/meeting-recorder.desktop` so the app appears in the system app menu

An `uninstall.sh` script is also provided that removes the venv, launcher, and `.desktop` entry.
