╭───────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ Plan to implement                                                                                     │
│                                                                                                       │
│ Meeting Recorder App — Implementation Plan                                                            │
│                                                                                                       │
│ Context                                                                                               │
│                                                                                                       │
│ Greenfield Python + GTK3 desktop app for Linux (Debian-based). Records mic + system audio, then       │
│ transcribes and summarizes using AI APIs (Gemini, OpenAI, or Anthropic). Full requirements in         │
│ /home/dipakyadav/Projects/Test/mint-meeting-recorder-applet/requirements.md.                          │
│                                                                                                       │
│ ---                                                                                                   │
│ Project File Structure                                                                                │
│                                                                                                       │
│ mint-meeting-recorder-applet/                                                                         │
│ ├── requirements.txt                                                                                  │
│ ├── install.sh                                                                                        │
│ ├── uninstall.sh                                                                                      │
│ ├── meeting-recorder.desktop.template                                                                 │
│ └── src/                                                                                              │
│     └── meeting_recorder/                                                                             │
│         ├── __init__.py                                                                               │
│         ├── __main__.py              # Entry point                                                    │
│         ├── app.py                   # Gtk.Application subclass                                       │
│         ├── ui/                                                                                       │
│         │   ├── main_window.py       # Main window + state machine                                    │
│         │   ├── settings_dialog.py   # Settings Gtk.Dialog (tabbed)                                   │
│         │   ├── tray.py              # AyatanaAppIndicator3 + pystray fallback                        │
│         │   └── notifications.py     # libnotify wrapper                                              │
│         ├── audio/                                                                                    │
│         │   ├── devices.py           # pactl device enumeration + validation                          │
│         │   ├── recorder.py          # Recording thread, subprocess lifecycle                         │
│         │   └── mixer.py             # ffmpeg command builder                                         │
│         ├── processing/                                                                               │
│         │   ├── pipeline.py          # Orchestrates transcription → summarization                     │
│         │   ├── transcription.py     # TranscriptionProvider protocol + factory                       │
│         │   ├── summarization.py     # SummarizationProvider protocol + factory                       │
│         │   └── providers/                                                                            │
│         │       ├── gemini.py        # Handles both tasks in one call if configured                   │
│         │       ├── whisper.py       # OpenAI Whisper + auto-chunking for >25MB                       │
│         │       ├── gpt4o.py         # OpenAI GPT-4o summarization                                    │
│         │       └── claude.py        # Anthropic Claude summarization                                 │
│         ├── detection/                                                                                │
│         │   ├── call_detector.py     # Coordinator daemon thread                                      │
│         │   ├── process_watcher.py   # psutil polling every 5s                                        │
│         │   └── audio_watcher.py     # pactl subscribe stream monitoring                              │
│         ├── config/                                                                                   │
│         │   ├── settings.py          # Load/save ~/.config/meeting-recorder/config.json               │
│         │   └── defaults.py          # Default values, model lists, known call processes              │
│         └── utils/                                                                                    │
│             ├── filename.py          # Output path generation with title sanitization                 │
│             └── glib_bridge.py       # GLib.idle_add / timeout_add wrappers                           │
│                                                                                                       │
│ ---                                                                                                   │
│ Key Technical Decisions                                                                               │
│                                                                                                       │
│ ┌────────────────┬─────────────────────────────────────────────────────────────────────────────┐      │
│ │      Area      │                                  Decision                                   │      │
│ ├────────────────┼─────────────────────────────────────────────────────────────────────────────┤      │
│ │ Audio capture  │ subprocess parec (mic + sink monitor) piped into ffmpeg amix → MP3          │      │
│ ├────────────────┼─────────────────────────────────────────────────────────────────────────────┤      │
│ │ Pause/Resume   │ SIGSTOP/SIGCONT on both parec processes; ffmpeg blocks naturally            │      │
│ ├────────────────┼─────────────────────────────────────────────────────────────────────────────┤      │
│ │ GTK version    │ GTK3 (PyGObject) — widest compatibility, AppIndicator support               │      │
│ ├────────────────┼─────────────────────────────────────────────────────────────────────────────┤      │
│ │ System tray    │ AyatanaAppIndicator3 (confirmed installed); pystray fallback if unavailable │      │
│ ├────────────────┼─────────────────────────────────────────────────────────────────────────────┤      │
│ │ Threading      │ threading.Thread + GLib.idle_add for all UI updates from background threads │      │
│ ├────────────────┼─────────────────────────────────────────────────────────────────────────────┤      │
│ │ MP3 encoding   │ ffmpeg -codec:a libmp3lame -q:a 4 (libmp3lame confirmed in system ffmpeg)   │      │
│ ├────────────────┼─────────────────────────────────────────────────────────────────────────────┤      │
│ │ Gemini SDK     │ google-genai package (NOT deprecated google-generativeai)                   │      │
│ ├────────────────┼─────────────────────────────────────────────────────────────────────────────┤      │
│ │ Call detection │ psutil process polling + subprocess pactl subscribe stdout parsing          │      │
│ └────────────────┴─────────────────────────────────────────────────────────────────────────────┘      │
│                                                                                                       │
│ ---                                                                                                   │
│ Implementation Order                                                                                  │
│                                                                                                       │
│ Phase 1 — Foundation                                                                                  │
│                                                                                                       │
│ 1. config/defaults.py — constants, schema                                                             │
│ 2. config/settings.py — load/save with chmod 600                                                      │
│ 3. utils/filename.py — path generation                                                                │
│ 4. utils/glib_bridge.py — GTK thread bridge                                                           │
│ 5. __main__.py + app.py — app skeleton, startup validation                                            │
│                                                                                                       │
│ Phase 2 — Audio                                                                                       │
│                                                                                                       │
│ 6. audio/devices.py — pactl-based device enumeration + validation                                     │
│ 7. audio/mixer.py — ffmpeg command builder (pure, no subprocesses)                                    │
│ 8. audio/recorder.py — recording thread, named pipes, subprocess lifecycle, timer                     │
│                                                                                                       │
│ Phase 3 — UI                                                                                          │
│                                                                                                       │
│ 9. ui/main_window.py — state machine (IDLE → RECORDING → PAUSED → PROCESSING → IDLE), timer, status   │
│ messages, error bar                                                                                   │
│ 10. ui/tray.py — tray icon with state-aware context menu                                              │
│ 11. ui/notifications.py — libnotify wrapper                                                           │
│ 12. ui/settings_dialog.py — tabbed settings dialog                                                    │
│                                                                                                       │
│ Phase 4 — AI Processing                                                                               │
│                                                                                                       │
│ 13. processing/transcription.py + processing/summarization.py — protocols + factories                 │
│ 14. processing/providers/gemini.py — file upload, polling, single-call dual-task, fallback to         │
│ separate calls                                                                                        │
│ 15. processing/providers/whisper.py — transcription + auto-chunking for files >25MB (use ffmpeg to    │
│ split into 20MB overlapping segments, transcribe each, concatenate)                                   │
│ 16. processing/providers/gpt4o.py + processing/providers/claude.py                                    │
│ 17. processing/pipeline.py — orchestrator with status callbacks                                       │
│                                                                                                       │
│ Phase 5 — Call Detection                                                                              │
│                                                                                                       │
│ 18. detection/process_watcher.py — psutil polling                                                     │
│ 19. detection/audio_watcher.py — pactl subscribe stdout reader                                        │
│ 20. detection/call_detector.py — coordinator, 5-min deduplication                                     │
│                                                                                                       │
│ Phase 6 — Installation                                                                                │
│                                                                                                       │
│ 21. requirements.txt                                                                                  │
│ 22. install.sh + uninstall.sh                                                                         │
│ 23. meeting-recorder.desktop.template                                                                 │
│                                                                                                       │
│ ---                                                                                                   │
│ Critical Implementation Notes                                                                         │
│                                                                                                       │
│ Named Pipe Startup Order (audio/recorder.py)                                                          │
│                                                                                                       │
│ Start parec processes before ffmpeg to avoid deadlock on pipe open. Use ffmpeg -thread_queue_size 512 │
│  to handle jitter. Named pipes created with os.mkfifo in a temp dir, cleaned up on stop.              │
│                                                                                                       │
│ GTK Thread Safety                                                                                     │
│                                                                                                       │
│ Never touch any Gtk.* object outside the main thread. All background threads post back via            │
│ GLib.idle_add. Callbacks must return GLib.SOURCE_REMOVE. Add assert threading.current_thread() is     │
│ threading.main_thread() at top of all UI update methods.                                              │
│                                                                                                       │
│ Gemini Single-Call Path                                                                               │
│                                                                                                       │
│ When both transcription and summarization are set to Gemini:                                          │
│ - Upload audio via Files API, poll until ACTIVE                                                       │
│ - Single prompt requesting --- TRANSCRIPT --- and --- NOTES --- sections                              │
│ - Parse response on those delimiters                                                                  │
│ - Show "Uploading...", "Processing..." status messages during polling                                 │
│                                                                                                       │
│ Whisper Auto-Chunking (processing/providers/whisper.py)                                               │
│                                                                                                       │
│ If file size > 24MB:                                                                                  │
│ 1. Use ffmpeg to split into N overlapping 20MB segments (30s overlap)                                 │
│ 2. Transcribe each segment via Whisper API                                                            │
│ 3. Deduplicate overlap by trimming the first 30s from each subsequent segment's transcript            │
│ 4. Concatenate results                                                                                │
│                                                                                                       │
│ Config Schema (~/.config/meeting-recorder/config.json)                                                │
│                                                                                                       │
│ {                                                                                                     │
│   "transcription_service": "gemini",                                                                  │
│   "summarization_service": "gemini",                                                                  │
│   "gemini_api_key": "",                                                                               │
│   "gemini_model": "gemini-1.5-flash",                                                                 │
│   "openai_api_key": "",                                                                               │
│   "openai_transcription_model": "whisper-1",                                                          │
│   "openai_summarization_model": "gpt-4o",                                                             │
│   "anthropic_api_key": "",                                                                            │
│   "anthropic_model": "claude-sonnet-4-6",                                                             │
│   "output_folder": "~/meetings",                                                                      │
│   "call_detection_enabled": false                                                                     │
│ }                                                                                                     │
│                                                                                                       │
│ Settings Dialog Validation                                                                            │
│                                                                                                       │
│ - Anthropic selected as transcription service → show inline red warning, disable OK                   │
│ - Missing API key for selected service → show inline warning, disable OK                              │
│ - chmod 600 enforced on every save                                                                    │
│                                                                                                       │
│ System Tray on GNOME                                                                                  │
│                                                                                                       │
│ AyatanaAppIndicator3 requires gnome-shell-extension-appindicator on GNOME. install.sh detects GNOME   │
│ (checks $XDG_CURRENT_DESKTOP) and prints a warning. App works fully without tray — it's a progressive │
│  enhancement.                                                                                         │
│                                                                                                       │
│ ---                                                                                                   │
│ Python Dependencies (requirements.txt)                                                                │
│                                                                                                       │
│ google-genai>=0.8.0       # Gemini (new unified SDK — NOT google-generativeai)                        │
│ openai>=1.0.0             # Whisper + GPT-4o                                                          │
│ anthropic>=0.25.0         # Claude                                                                    │
│ psutil>=5.9.0             # Process enumeration                                                       │
│ pystray>=0.19.0           # Tray fallback                                                             │
│ Pillow>=10.0.0            # Required by pystray                                                       │
│                                                                                                       │
│ System APT Dependencies (install.sh)                                                                  │
│                                                                                                       │
│ python3 python3-venv python3-gi python3-gi-cairo                                                      │
│ gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1                                                        │
│ libayatana-appindicator3-1 gir1.2-notify-0.7 libnotify4                                               │
│ ffmpeg pulseaudio-utils pipewire-pulse                                                                │
│ Most of these are already installed on this machine.                                                  │
│                                                                                                       │
│ ---                                                                                                   │
│ Verification Plan                                                                                     │
│                                                                                                       │
│ 1. Run python -m meeting_recorder from the venv — app opens with Record button                        │
│ 2. Press Record — timer starts, Pause + Stop appear                                                   │
│ 3. Press Pause — timer freezes, Resume appears                                                        │
│ 4. Press Stop — processing state shown; transcript + notes files created in ~/meetings/               │
│ 5. Configure Gemini key in Settings, re-run step 4 — actual AI output produced                        │
│ 6. Test system tray: right-click tray icon, use Start/Stop from menu                                  │
│ 7. Enable call detection, open Discord — notification appears within 10s                              │
│ 8. Run install.sh, launch app from app menu — confirms desktop integration works                      │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────╯

