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
5.1 If changes are only in linux app create tag for linux release only (eg. v1.2.3)
5.2 If changes are only in android app create tag for android release only (eg. android-1.2.3)
5.3 If changes are in both, create both releases

This applies to all agents (Claude, Gemini, etc.) — no direct pushes to `main` under any circumstances.

---

## Keep documentation in sync — IMPORTANT

Whenever a change affects user-facing behavior, features, architecture, commands, conventions, or test boundaries, update the relevant docs **in the same PR** so they never drift from the code:

- `README.md` — user-facing features, setup, and workflows (Linux and Android sections)
- `CLAUDE.md` and `GEMINI.md` — architecture, commands, conventions, and test-coverage boundaries

Before opening a PR, re-read these three files and reconcile anything the change made inaccurate (new screens/services, renamed flows, new settings, new tests, changed defaults). Treat doc updates as part of "done," not a follow-up.

---

## Keep tests meaningful — IMPORTANT

For every change, add or update tests when doing so is meaningful — treat it as part of "done," not a follow-up. "Meaningful" means the test would actually catch a regression in the behavior you changed:

- New or changed logic with a testable contract (parsing, decisions, data transforms, repository/IO, API request/response handling) → add or update unit tests that cover the new behavior and its edge cases.
- Fixing a bug → add a test that fails without the fix, so it can't silently regress.
- When the meaningful logic is tangled with hard-to-test platform code (Android ViewModels/Compose, GTK UI), **extract the pure logic into a standalone function and test that** — this is the established pattern (e.g. `RecordingStopDecision.kt` + `RecordingStopDecisionTest`, `GenerateActionDecision.kt` + `GenerateActionDecisionTest`). See the test-coverage boundaries below for what is and isn't unit-tested.
- Run the relevant suite before opening a PR: `pytest` (Linux) and/or `./gradlew test` (Android).

Skip new tests only when a change genuinely has no testable behavior (docs, comments, pure formatting, trivial constant tweaks) — and say so briefly rather than silently omitting them.

---

## What this repo is

A monorepo with two independent apps that share the same on-disk recording format (`YYYY/MonthName/DD/HH-MM[_title]/recording.m4a|mp3 + transcript.md + notes.md`):

- `linux/` — GTK4 + libadwaita desktop applet (Python), runs on Debian/Ubuntu/Fedora/Arch
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

# Lint + format (CI enforces both; config in pyproject.toml)
ruff check linux/
ruff format linux/

# Type check (CI enforces; strict on processing/, services/, config/)
mypy linux/src/meeting_recorder/processing linux/src/meeting_recorder/services linux/src/meeting_recorder/config
```

`pyproject.toml` sets `testpaths = ["linux/tests"]` and `pythonpath = ["linux/src"]`, so `pytest` works from the repo root. It also holds the ruff config (line length 100; `E402` ignored because PyGObject needs `gi.require_version()` before `gi.repository` imports) and the mypy config (strict mode on the headless `processing`/`services`/`config` packages — new code there must be fully annotated; GTK-bound `ui`/`audio`/`detection` are checked leniently).

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

# Kotlin lint (CI enforces; config in android/.editorconfig — intellij_idea
# style, @Composable functions exempt from function-naming)
./gradlew ktlintCheck
./gradlew ktlintFormat   # autofix

# Install to connected emulator / device
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

---

## Linux architecture

**Entry:** `__main__.py` → `app.py` starts the GLib main loop and wires the tray icon, main window, and call detector together.

**Audio recording** (`audio/`):
- `recorder.py` runs a single `ffmpeg` subprocess reading PulseAudio/PipeWire sources directly (`-f pulse`); `mixer.py` builds the command — mic+system mode `amerge`s mic (left channel) and sink monitor (right channel) into a true-stereo MP3 with a `highpass=f=80` filter, preserving speaker separation for transcription. Device names are resolved once in `start()` via `devices.py` (`pactl`).
- Pause/resume works via **segments**: pause terminates ffmpeg cleanly (saving the current segment), resume spawns a new ffmpeg writing the next segment, and stop concatenates all segments with ffmpeg's concat demuxer so paused intervals are excluded. `stop()` blocks until ffmpeg exits and segments are merged; a monitor thread reports unexpected ffmpeg death via `on_error`.
- Two modes: mic+system (`Record (Headphones)`) and mic-only (`Record (Speaker)` — the monitor is skipped to avoid echo).

**Recording state machine** (`core/state_machine.py`): `State` (IDLE/RECORDING/PAUSED/COUNTDOWN) plus the pure `can_transition()` legality table — `MainWindow._transition()` validates against it (logs an error on an illegal jump). `core/job.py` holds the `Job` dataclass (`JobStatus` enum, per-job `CancelToken`) and `actions_for_status()`, the pure policy for which buttons a job row offers. `State` is re-exported from `ui/main_window.py` for existing importers.

**Recording lifecycle** (`core/recording_controller.py`): `RecordingController` owns the Recorder instance, the stop/processing countdown, and the authoritative lifecycle `State`; `MainWindow` only renders state changes (`_apply_state`) and forwards button clicks (`window._state` is a read-through property — app.py/tray still read it). Callbacks: `on_state`/`on_error`/`on_commit(PendingRecording)`/`on_saved`/`on_discarded`/`on_countdown`; `on_timer` and `on_recorder_error` arrive on recorder worker threads and the window wraps them with `idle_call`. GTK dependencies (countdown scheduler, recorder factory, device validation) are injected, so the whole lifecycle is unit-testable headless. `make_job_label()` and `settings.api_key_error()` are the extracted pure helpers.

**Job queue & persistence** (`core/job_manager.py`): `JobManager` owns the job list and persists every change to `$XDG_STATE_HOME/meeting-recorder/jobs.json` (atomic tmp+rename; cancelled jobs excluded). On startup `load_persisted()` re-offers interrupted work: jobs that were PROCESSING when the app died come back as ERROR rows ("Interrupted…") with Retry (pure policy `restore_status()`), ERROR jobs restore as-is, DONE jobs are pruned — mirrors Android's `recoverOrphanedRecordings()`. Main-thread only, like all job mutations.

**Background work** (`core/task_runner.py`): all off-main-thread work goes through the app-wide `TaskRunner` (created in `app.py`, passed to `MainWindow`) — never raw `threading.Thread`. `submit(fn, *args, on_done=, on_error=, description=)` runs `fn` on a tracked daemon thread and routes the result/exception back to the GTK main thread; a worker exception with no `on_error` is still logged, and main-thread callbacks are wrapped so their own exceptions are logged instead of being swallowed by GLib. `app.do_shutdown()` calls `runner.shutdown(grace_seconds=10)`, which joins running tasks and logs any it had to abandon. Workers must only *read* job state; all mutations happen in the main-thread callbacks (this is what makes `_Job` race-free without locks). `CancelToken` provides cooperative cancellation. The main-thread scheduler is injectable, so the module is unit-testable without GLib.

**AI processing** (`processing/`):
- `Pipeline` runs transcription then summarization as separate calls (a single dual-prompt call was removed because the model would cut transcription short to save output budget for notes). `Pipeline.run(cancel_token=)` takes an optional `CancelToken` and checks it **between stages** (`PipelineCancelled` is raised; an in-flight network call still completes but no further stage starts and nothing is written) — each `_Job` in the main window carries its own token, cancelled from the job row / tray.
- Transient network failures (timeouts, connection resets, 5xx, 429) are retried with exponential backoff via `core/retry.py:retry_on_transient()` — used around the Gemini upload/generate calls and the Ollama generate call. Permanent errors (bad key, 4xx, model errors) fail immediately.
- `config/settings.py:gemini_key_warning()` is a pure format check (keys start with "AIza") surfaced as an alert when saving Settings, so a mispasted key is caught at save time instead of as a failed job.
- `transcription.py` / `summarization.py` expose factory functions (`create_transcription_provider`, `create_summarization_provider`) that return a provider based on config.
- Providers: `providers/gemini.py`, `providers/whisper.py`, `providers/whisper_cpp.py`, `providers/ollama.py`. Each implements `.transcribe()` or `.summarize()` and optionally `.unload()` to free GPU VRAM.
- Before running a local Whisper engine (`whisper` or `whisper_cpp`), the pipeline evicts any loaded Ollama models from VRAM.

**Call detection** (`detection/`): `AudioWatcher` runs `pactl subscribe` on its own daemon thread and calls back on new mic-capture streams (pure matcher `is_call_start_event()`); if pactl dies it is **restarted with exponential backoff** (1 s → 60 s cap, reset after a healthy minute). `CallDetector` wraps it with a notification dedup window. Injectable `spawn_fn`/`sleep_fn`/`monotonic_fn` make it unit-testable.

**Bare-bones, opt-in local engines:** The base install is **Gemini-only** — `linux/requirements.txt` carries no local-engine deps. Local capabilities are installed on demand from **Settings → Models**:
- `whisper` (faster-whisper) — installed via `WhisperEngineInstaller` (pip into the app venv). CTranslate2-backed, so **NVIDIA/CPU only**. `providers/whisper.py:_detect_device()` probes CUDA, else CPU.
- `whisper_cpp` — a from-source whisper.cpp build for **AMD (ROCm/Vulkan), Apple (Metal), NVIDIA (CUDA), or CPU**. `services/whisper_cpp_service.py` holds the pure helpers `detect_gpu_backend()` and `build_cmake_command(backend)`, the `WhisperCppBuilder` (toolchain + clone + cmake), and `WhisperCppStatusChecker`/`WhisperCppModelDownloader` (GGML files). The provider parses `whisper-cli` JSON via the pure `parse_whisper_cpp_output()`.
- GPU runtime installs are vendor-aware: `services/system_installer.py` has `detect_gpu_vendor()`, `CudaInstaller` (NVIDIA), and `RocmInstaller` (AMD); the Settings "GPU Acceleration" section picks the right one.
- **Installer security conventions** (`services/system_installer.py`): no `os.system` — commands are argv lists run without a shell and logged before execution; privilege elevation via `build_privileged_command()` (`pkexec` polkit dialog, `sudo` fallback) with only fixed/validated shell snippets; the Ollama install script is downloaded over HTTPS to a temp file with its SHA-256 logged, then executed from disk — never `curl | sh`. Test seams are `which_fn`/`run_fn`/`capture_fn`/`fetch_fn`.

**Config:** `~/.config/meeting-recorder/config.json`, `chmod 600`. Empty string for any prompt key = use built-in default (defined in `config/defaults.py`). **API key storage:** when a D-Bus Secret Service is available (GNOME Keyring/KWallet), `settings.save()` stores the Gemini key there via `config/keyring_store.py:KeyringStore` and writes only the `@keyring` sentinel to config.json; `settings.load()` resolves the sentinel back. `settings.migrate_key_to_keyring()` runs once at startup (`app.do_startup`) to move a legacy plaintext key. Without a keyring everything falls back to plaintext-in-chmod-600 exactly as before. The `secretstorage` module is injectable for tests.

**GTK4 / libadwaita toolkit notes:** The UI is **GTK4 + libadwaita** (`Adw`). `app.py` is an `Adw.Application` (auto-inits libadwaita → Adwaita stylesheet + light/dark portal). There is no blocking `Gtk.Dialog.run()` — message/confirm dialogs use the async `Gtk.AlertDialog` and file/folder pickers use the async `Gtk.FileDialog` (callbacks via `Gio.AsyncReadyCallback`). GTK4 removed `Gtk.Container`, so `pack_start`/`add`/`get_children` are gone — `ui/` builds with `append`/`set_child` and the shared helpers in `utils/gtk_compat.py` (`iter_children`, `remove_all_children`). Visibility uses `set_visible()` (no `show_all`); inline events use `Gtk.GestureClick`/`EventControllerKey`/`EventControllerFocus`. Adwaita idioms: `Adw.ApplicationWindow`+`Adw.ToolbarView`+`Adw.HeaderBar`+`Adw.ViewStack`/`ViewSwitcher`, `Adw.PreferencesGroup` rows (`ActionRow`/`SwitchRow`/`ComboRow`/`EntryRow`/`PasswordEntryRow`), `Adw.ToastOverlay`/`Toast` for transient errors, `.boxed-list`/`.pill`/`.flat` style classes, and `Adw.Clamp` for centred content.

**UI** (`ui/`): `main_window.py` (recording controls; job rows rendered by `ui/jobs_panel.py:JobsPanel` from the pure `actions_for_status()` policy; errors surfaced via the pure `core/errors.py:error_presentation()` policy — actionable configuration problems get a modal `Gtk.AlertDialog`, transient/runtime failures get a toast; `present_window()` re-shows + `unminimize()` + `present()`), `settings_dialog.py` (a thin `Adw.Window` shell — Cancel/ViewSwitcher/Save header, page instantiation, and the save flow; each tab lives in its own module under `settings_pages/`: `general.py`/`models.py`/`prompts.py` page classes expose `.widget` and `.apply(cfg)`, with shared row helpers + `IdComboRow` in `settings_pages/widgets.py` (re-exported from `settings_dialog` for compatibility); `ModelsPage` takes the same injected-service seams the dialog passes through; `compute_section_visibility()` is the pure Models-tab visibility policy; `on_saved` callback runs the post-save reconfiguration since the dialog is modeless), `model_row_grid.py` (`Adw.PreferencesGroup` of model `ActionRow`s with the same setter API), `meeting_explorer.py` (past meetings browser; `.boxed-list` rows; double-click-to-rename via `GestureClick`), `tray.py` (system tray icon). The tray is a **pure-DBus StatusNotifierItem** built on `Gio.DBusConnection` (no GTK widgets, no new dependency) implementing `org.kde.StatusNotifierItem` + `com.canonical.dbusmenu`; it registers with the session `StatusNotifierWatcher` and re-registers if the host restarts. Pure helpers `icon_for_state()` and `build_menu_model()` hold the icon/menu policy — `icon_for_state()` returns the bundled icon basename per state (idle/recording/paused/processing), and `tray.py` renders the matching custom PNGs from `assets/tray/` as a raw ARGB `IconPixmap` (not a theme `IconName`), so the branded tray artwork shows on every host and when running from source. The app/launcher/window icon ships in `assets/icons/hicolor/` (scalable SVG + PNG sizes, named `meeting-recorder` — the `Icon=` key the desktop file references) and is installed into the system/user hicolor theme by the install scripts and packaging; at startup `app.py:_setup_app_icon()` also adds the bundled tree to the GTK icon-theme search path and calls `set_default_icon_name("meeting-recorder")` so the icon resolves when running from source. The installed desktop file is named after the application id (`io.github.dipakmdhrm.MeetingRecorder.desktop`, matching `APP_ID` in `config/defaults.py`, with `StartupWMClass`) so the GNOME/Wayland shell (and Dash to Panel) maps a running window to it and shows the app icon instead of a generic one. `MainWindow.on_use_existing_clicked` delegates its in-tree-reuse vs. copy decision to the pure `utils/recording_import.py:resolve_existing_recording_target()`.

**Import convention:** Provider files use 3-dot relative imports (`from ...config.defaults import …`). Files outside `meeting_recorder/` use absolute imports (`from meeting_recorder.config.defaults import …`).

---

## Android architecture

**Application class & DI:** `MeetingRecorderApp` owns an `AppContainer` (manual dependency container — deliberately no Hilt/Koin at this size) that wires the app-wide singletons: `Config` (SharedPreferences wrapper), `MeetingRepository` (file-system meeting store rooted at `Documents/Meetings/`), a shared `GeminiClient`, and a shared `MeetingProcessor`. ViewModels get these as constructor parameters through the shared `appViewModelFactory` (`viewModelFactory { initializer { … } }` reading the app from `APPLICATION_KEY`) instead of casting `application`; only ViewModels that genuinely need a Context stay `AndroidViewModel` (`MainViewModel` — service start/getString/contentResolver; `MeetingsViewModel` — getString), while `MeetingDetailViewModel` and `SettingsViewModel` are plain `ViewModel`s. Startup orphan recovery (`recoverOrphanedRecordings()`) runs in an application-scoped coroutine (`SupervisorJob() + Dispatchers.IO`), not a raw `Thread`.

**Navigation:** `AppNavGraph` (Compose Navigation) with four routes:
- `main` → `MainScreen` (record button, status)
- `settings` → `SettingsScreen` (General tab + Prompts tab)
- `meetings` → `MeetingsScreen` (list of past meetings)
- `meeting_detail/{meetingPath}` → `MeetingDetailScreen` (Notes / Transcript / Audio tabs)

Routes are defined once in `ui/nav/Routes.kt` (`Routes.MAIN`, `Routes.meetingDetail(path)`, `Routes.MEETING_DETAIL_PATTERN`, …) — no call site hand-builds route strings. File system paths are passed as nav arguments with `/` encoded as `%2F` via the pure `Routes.encodeMeetingPath`/`decodeMeetingPath` helpers (JVM-tested in `RoutesTest`).

**Settings save model:** Each settings tab holds local draft state in the Composable. The ViewModel setters write directly to `Config`/SharedPreferences. The Save button is what calls the setters — nothing is persisted on keystroke. Empty string stored for a prompt = use built-in default (same convention as Linux).

**Gemini API** (`data/GeminiClient.kt`): Manual OkHttp implementation (no Gemini SDK). Flow: resumable upload init → upload bytes → poll `GET /v1beta/files/{id}` until `state == ACTIVE` → `generateContent`. The poll response is a flat JSON object (not wrapped in a `"file"` key). The API key is sent via the `x-goog-api-key` header (never as a `?key=` URL param), and each network step is individually retried on transient failures (IOException, HTTP 5xx/429) with exponential backoff (2s doubling, 2 retries; other 4xx fail immediately) via an injectable `delayFn` seam; retry attempts and poll iterations check coroutine cancellation. Exposes `transcribe()`, `summarize()`, and `generateTitle()`. Request bodies and response parsing use `kotlinx.serialization` wire DTOs (private, at the bottom of `GeminiClient.kt`) with the shared lenient `Json` instance (`ignoreUnknownKeys` + `isLenient`, defined in `data/MeetingMeta.kt`) — field names must match the Gemini REST API exactly.

**Meeting processing** (`data/MeetingProcessor.kt`): The process→save workflow shared by the record flow (`MainViewModel`) and the detail-screen generation flows (`MeetingDetailViewModel`) lives in one JVM-testable class: `transcribeAndSummarize()` (upload → transcribe → summarize, returns `ProcessingResult`), `summarizeTranscript()` (notes-only, no re-upload), `generateTitle()` (best-effort — logs and returns null instead of throwing, so a title failure never fails the flow), and `saveResults()`/`saveNotes()` (transcript.md/notes.md + `saveMeetingMeta`). Dependencies are constructor-injected: `GeminiClient`, a `MeetingStore` (the minimal storage interface `MeetingRepository` implements — the seam for a future SAF backend), a `CoroutineDispatcher`, and a `logWarn` sink (so tests never touch `android.util.Log`). The ViewModels keep only state management and platform glue (recording service, lock files, countdowns, content resolvers). The pure extension→MIME and MIME-alias mappings are top-level functions in `util/Mime.kt`, shared by both ViewModels.

**Audio recording** (`audio/`): Recording runs in a foreground service (`RecordingService`, `foregroundServiceType=microphone`) wrapping `AudioRecorder` (`MediaRecorder` → MPEG_4/AAC → `.m4a`). Bitrate is configurable via `Config.audioQuality` (`AudioQuality` enum; default **Low / 64 kbps**), not fixed. The service keeps capturing through brief interruptions; if the OS silences the mic mid-recording (e.g. an answered call) the audio is kept but flagged so it is **not** transcribed (the user is warned instead). An optional "Do Not Disturb while recording" setting silences notifications during capture. `RecordingStopDecision.decideStopOutcome(...)` is a pure helper that decides what `MainViewModel.stopRecording()` does once the recorder stops (missing/empty/silenced/countdown/process).

**Importing & recovery:** "Use Existing Recording" (`MainViewModel.processExistingRecording`) imports an external audio file, or re-processes one already inside a meeting dir in place (`processInPlace`). When post-recording processing fails, `saveAudioOnlyAfterFailure` keeps the raw audio in the library as an audio-only meeting; `MeetingRepository.recoverOrphanedRecordings()` (run at launch) clears stale `.recording` locks so crashed/failed recordings reappear instead of being lost.

**Detail-screen generation:** `MeetingDetailScreen` / `MeetingDetailViewModel` can generate or regenerate content for a meeting already in the library — *Generate transcript & notes* (when audio exists), *Generate notes* (reusing an existing transcript, no re-upload), and *Regenerate notes* — delegating to the same `MeetingProcessor` workflows as the record flow. Playback uses `MediaPlayer` in the same ViewModel; the Audio tab is only shown when `hasAudio` is true. `GenerateActionDecision` is a pure helper for which empty-state button to offer.

**Storage:** `Documents/Meetings/YYYY/MonthName/DD/HH-MM[_title]/` on external storage (`MANAGE_EXTERNAL_STORAGE` permission required). `meeting.json` metadata is (de)serialized via the `@Serializable` `data/MeetingMeta.kt` DTO (exact on-disk field names `title`/`duration_seconds` — shared with the Linux app, do not rename) using a lenient `Json` (`ignoreUnknownKeys`); malformed metadata is still skipped per-entry, never fatal, and `renameMeeting` edits the raw JSON tree so fields it doesn't own (including Linux-written keys) survive a rename. Meetings can be renamed and deleted from `MeetingsScreen`. The `Documents` root is resolved in `AppContainer` via `StorageManager.primaryStorageVolume.directory` (non-deprecated; same `/storage/emulated/0` root the docs-deprecated `Environment.getExternalStoragePublicDirectory()` used).

`MANAGE_EXTERNAL_STORAGE` is a **deliberate decision**, not an oversight: the app is distributed as a GitHub APK (not on the Play Store), and the shared, user-visible `Documents/Meetings/` tree survives app uninstall and is the same on-disk format the Linux app reads and writes. If Play Store distribution ever becomes a goal, the migration path is a SAF tree grant (`ACTION_OPEN_DOCUMENT_TREE` over the same `Documents/Meetings/` directory), which preserves the shared on-disk format — until then, do not swap the permission model.

---

## Test coverage boundaries

### Linux
`linux/tests/core/test_task_runner.py` covers `TaskRunner` (result/error routing, logging of unhandled worker and callback exceptions, graceful shutdown with abandoned-task reporting, submit-after-shutdown) and `CancelToken`, using an injected immediate scheduler instead of GLib. `linux/tests/core/test_retry.py` covers `retry_on_transient` and the `is_transient` classifier (backoff schedule, permanent-vs-transient, HTTP status attributes). `linux/tests/core/test_state_machine.py` covers the `can_transition` legality table (allowed/illegal/self-transitions, exhaustiveness). `linux/tests/core/test_job.py` covers `Job` defaults, per-job tokens, and the `actions_for_status` row policy. `linux/tests/core/test_job_manager.py` covers JobManager persistence round-trips, atomic writes, cancelled-job exclusion, and startup recovery (interrupted→error+retry, done pruned, id collision avoidance, corrupt/malformed state tolerated) plus the pure `restore_status` policy. `linux/tests/core/test_errors.py` covers the `error_presentation` dialog-vs-toast policy. `linux/tests/core/test_recording_controller.py` covers the full recording lifecycle headless (start validation/failure paths, pause/resume, stop with and without countdown, countdown tick/cancel, cancel+save, cancel+discard with audio deletion, abort recovery) with fake recorder/scheduler. `linux/tests/processing/test_pipeline.py` covers Pipeline fail-fast and cancel-token guards. `linux/tests/config/test_settings_validation.py` covers `gemini_key_warning`. `linux/tests/config/test_keyring.py` covers `KeyringStore` (roundtrip/replace/delete/unavailable/locked-collection) and the settings keyring integration (sentinel on disk, plaintext fallback, clear-deletes-secret, one-time migration) with an in-memory secretstorage fake. `linux/tests/detection/test_audio_watcher.py` covers the pure `is_call_start_event` matcher and the watcher's restart/backoff/stop behavior with fake processes. Tests in `linux/tests/services/` cover `OllamaService`, `WhisperService`, and `SystemInstaller` (now including `RocmInstaller`, `WhisperEngineInstaller`, and `detect_gpu_vendor`) with mocks/temp dirs. `linux/tests/services/test_whisper_cpp_service.py` covers `detect_gpu_backend`, `build_cmake_command`, `WhisperCppBuilder` (with per-backend + cross-distro branch isolation), and the GGML status/downloader. `linux/tests/processing/providers/test_whisper_cpp.py` covers the pure `parse_whisper_cpp_output`, the provider's injected-runner `transcribe` flow, and the `whisper_cpp` factory wiring. `linux/tests/processing/providers/test_ollama.py` covers `OllamaProvider.summarize` error handling (server error field, unreachable host, empty response, bounded timeout, transient-retry) via the injected `http_open` hook. `linux/tests/ui/test_tray.py` covers the pure tray helpers `icon_for_state` and `build_menu_model`. `linux/tests/ui/test_settings_visibility.py` covers `compute_section_visibility` (the Models-tab section/separator policy). `linux/tests/ui/test_existing_recording.py` covers `resolve_existing_recording_target` (the import in-tree-reuse vs. copy decision). GTK UI (the GTK4 widget construction in `ui/`, the async dialog callbacks, and the tray's D-Bus wiring) remains not unit-tested — pure decision logic is extracted into testable helpers/services per the pattern below.

### Android
JVM-only unit tests (no Robolectric) in `app/src/test/`:
- `ConfigTest` — validates constants and model list invariants
- `MeetingRepositoryTest` — full coverage of listing, parsing, creating, and saving meetings using `TemporaryFolder`, including meeting.json backward-compatibility fixtures (pre-migration org.json-written text, unknown-key tolerance, rename preserving foreign keys); tests deliberately keep the `org.json` test dependency as an *independent* parser to prove the kotlinx.serialization output stays format-compatible
- `GeminiClientTest` — full coverage of the upload→poll→generate flow using `MockWebServer`
- `MeetingProcessorTest` — the extracted process→save workflow end to end (`MockWebServer` + `TemporaryFolder`): happy path writes transcript.md/notes.md/meeting.json, title-generation failure returns null without failing the flow, notes-only path reuses the transcript without an upload, transcription errors propagate with nothing written
- `MimeTest` — the pure `util/Mime.kt` extension→MIME and MIME-alias mappings
- `RecordingStopDecisionTest` — covers `decideStopOutcome` branch ordering
- `GenerateActionDecisionTest` — covers the detail-screen empty-state button policy
- `RoutesTest` — covers the nav-route builders and the `%2F` meeting-path encode/decode round-trip

ViewModels (`MainViewModel`, `MeetingDetailViewModel`, `MeetingsViewModel`, `SettingsViewModel`) and all Compose UI are **not** unit-tested; they require Android platform APIs or the Compose testing framework, neither of which is in the current test setup. The established pattern is to **extract pure decision logic out of a ViewModel into a standalone function** (e.g. `RecordingStopDecision.kt`, `GenerateActionDecision.kt`) — or a whole workflow into a constructor-injected class (`MeetingProcessor`) — so the logic is unit-testable even though the ViewModel is not.
