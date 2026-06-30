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
- Providers: `providers/gemini.py`, `providers/whisper.py`, `providers/whisper_cpp.py`, `providers/ollama.py`. Each implements `.transcribe()` or `.summarize()` and optionally `.unload()` to free GPU VRAM.
- Before running a local Whisper engine (`whisper` or `whisper_cpp`), the pipeline evicts any loaded Ollama models from VRAM.

**Bare-bones, opt-in local engines:** The base install is **Gemini-only** — `linux/requirements.txt` carries no local-engine deps. Local capabilities are installed on demand from **Settings → Models**:
- `whisper` (faster-whisper) — installed via `WhisperEngineInstaller` (pip into the app venv). CTranslate2-backed, so **NVIDIA/CPU only**. `providers/whisper.py:_detect_device()` probes CUDA, else CPU.
- `whisper_cpp` — a from-source whisper.cpp build for **AMD (ROCm/Vulkan), Apple (Metal), NVIDIA (CUDA), or CPU**. `services/whisper_cpp_service.py` holds the pure helpers `detect_gpu_backend()` and `build_cmake_command(backend)`, the `WhisperCppBuilder` (toolchain + clone + cmake), and `WhisperCppStatusChecker`/`WhisperCppModelDownloader` (GGML files). The provider parses `whisper-cli` JSON via the pure `parse_whisper_cpp_output()`.
- GPU runtime installs are vendor-aware: `services/system_installer.py` has `detect_gpu_vendor()`, `CudaInstaller` (NVIDIA), and `RocmInstaller` (AMD); the Settings "GPU Acceleration" section picks the right one.

**Config:** `~/.config/meeting-recorder/config.json`, `chmod 600`. Empty string for any prompt key = use built-in default (defined in `config/defaults.py`).

**GTK4 / libadwaita toolkit notes:** The UI is **GTK4 + libadwaita** (`Adw`). `app.py` is an `Adw.Application` (auto-inits libadwaita → Adwaita stylesheet + light/dark portal). There is no blocking `Gtk.Dialog.run()` — message/confirm dialogs use the async `Gtk.AlertDialog` and file/folder pickers use the async `Gtk.FileDialog` (callbacks via `Gio.AsyncReadyCallback`). GTK4 removed `Gtk.Container`, so `pack_start`/`add`/`get_children` are gone — `ui/` builds with `append`/`set_child` and the shared helpers in `utils/gtk_compat.py` (`iter_children`, `remove_all_children`). Visibility uses `set_visible()` (no `show_all`); inline events use `Gtk.GestureClick`/`EventControllerKey`/`EventControllerFocus`. Adwaita idioms: `Adw.ApplicationWindow`+`Adw.ToolbarView`+`Adw.HeaderBar`+`Adw.ViewStack`/`ViewSwitcher`, `Adw.PreferencesGroup` rows (`ActionRow`/`SwitchRow`/`ComboRow`/`EntryRow`/`PasswordEntryRow`), `Adw.ToastOverlay`/`Toast` for transient errors, `.boxed-list`/`.pill`/`.flat` style classes, and `Adw.Clamp` for centred content.

**UI** (`ui/`): `main_window.py` (recording controls; jobs shown as `Adw.ActionRow`s; errors as toasts; `present_window()` re-shows + `unminimize()` + `present()`), `settings_dialog.py` (an `Adw.Window` with a Cancel/ViewSwitcher/Save header hosting `Adw.PreferencesGroup` tabs; `IdComboRow` wraps `Adw.ComboRow` to keep id-based combos; `compute_section_visibility()` is the pure Models-tab visibility policy; `on_saved` callback runs the post-save reconfiguration since the dialog is modeless), `model_row_grid.py` (`Adw.PreferencesGroup` of model `ActionRow`s with the same setter API), `meeting_explorer.py` (past meetings browser; `.boxed-list` rows; double-click-to-rename via `GestureClick`), `tray.py` (system tray icon). The tray is a **pure-DBus StatusNotifierItem** built on `Gio.DBusConnection` (no GTK widgets, no new dependency) implementing `org.kde.StatusNotifierItem` + `com.canonical.dbusmenu`; it registers with the session `StatusNotifierWatcher` and re-registers if the host restarts. Pure helpers `icon_for_state()` and `build_menu_model()` hold the icon/menu policy. `MainWindow.on_use_existing_clicked` delegates its in-tree-reuse vs. copy decision to the pure `utils/recording_import.py:resolve_existing_recording_target()`.

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

**Gemini API** (`data/GeminiClient.kt`): Manual OkHttp implementation (no Gemini SDK). Flow: resumable upload init → upload bytes → poll `GET /v1beta/files/{id}` until `state == ACTIVE` → `generateContent`. The poll response is a flat JSON object (not wrapped in a `"file"` key). Exposes `transcribe()`, `summarize()`, and `generateTitle()`.

**Audio recording** (`audio/`): Recording runs in a foreground service (`RecordingService`, `foregroundServiceType=microphone`) wrapping `AudioRecorder` (`MediaRecorder` → MPEG_4/AAC → `.m4a`). Bitrate is configurable via `Config.audioQuality` (`AudioQuality` enum; default **Low / 64 kbps**), not fixed. The service keeps capturing through brief interruptions; if the OS silences the mic mid-recording (e.g. an answered call) the audio is kept but flagged so it is **not** transcribed (the user is warned instead). An optional "Do Not Disturb while recording" setting silences notifications during capture. `RecordingStopDecision.decideStopOutcome(...)` is a pure helper that decides what `MainViewModel.stopRecording()` does once the recorder stops (missing/empty/silenced/countdown/process).

**Importing & recovery:** "Use Existing Recording" (`MainViewModel.processExistingRecording`) imports an external audio file, or re-processes one already inside a meeting dir in place (`processInPlace`). When post-recording processing fails, `saveAudioOnlyAfterFailure` keeps the raw audio in the library as an audio-only meeting; `MeetingRepository.recoverOrphanedRecordings()` (run at launch) clears stale `.recording` locks so crashed/failed recordings reappear instead of being lost.

**Detail-screen generation:** `MeetingDetailScreen` / `MeetingDetailViewModel` can generate or regenerate content for a meeting already in the library — *Generate transcript & notes* (when audio exists), *Generate notes* (reusing an existing transcript, no re-upload), and *Regenerate notes* — reusing `GeminiClient` and the same disk-write + `saveMeetingMeta` pattern as the record flow. Playback uses `MediaPlayer` in the same ViewModel; the Audio tab is only shown when `hasAudio` is true. `GenerateActionDecision` is a pure helper for which empty-state button to offer.

**Storage:** `Documents/Meetings/YYYY/MonthName/DD/HH-MM[_title]/` on external storage (`MANAGE_EXTERNAL_STORAGE` permission required). Meetings can be renamed and deleted from `MeetingsScreen`.

---

## Test coverage boundaries

### Linux
Tests in `linux/tests/services/` cover `OllamaService`, `WhisperService`, and `SystemInstaller` (now including `RocmInstaller`, `WhisperEngineInstaller`, and `detect_gpu_vendor`) with mocks/temp dirs. `linux/tests/services/test_whisper_cpp_service.py` covers `detect_gpu_backend`, `build_cmake_command`, `WhisperCppBuilder` (with per-backend + cross-distro branch isolation), and the GGML status/downloader. `linux/tests/processing/providers/test_whisper_cpp.py` covers the pure `parse_whisper_cpp_output`, the provider's injected-runner `transcribe` flow, and the `whisper_cpp` factory wiring. `linux/tests/ui/test_tray.py` covers the pure tray helpers `icon_for_state` and `build_menu_model`. `linux/tests/ui/test_settings_visibility.py` covers `compute_section_visibility` (the Models-tab section/separator policy). `linux/tests/ui/test_existing_recording.py` covers `resolve_existing_recording_target` (the import in-tree-reuse vs. copy decision). GTK UI (the GTK4 widget construction in `ui/`, the async dialog callbacks, and the tray's D-Bus wiring) remains not unit-tested — pure decision logic is extracted into testable helpers/services per the pattern below.

### Android
JVM-only unit tests (no Robolectric) in `app/src/test/`:
- `ConfigTest` — validates constants and model list invariants
- `MeetingRepositoryTest` — full coverage of listing, parsing, creating, and saving meetings using `TemporaryFolder`
- `GeminiClientTest` — full coverage of the upload→poll→generate flow using `MockWebServer`
- `RecordingStopDecisionTest` — covers `decideStopOutcome` branch ordering
- `GenerateActionDecisionTest` — covers the detail-screen empty-state button policy

ViewModels (`MainViewModel`, `MeetingDetailViewModel`, `SettingsViewModel`) and all Compose UI are **not** unit-tested; they require Android platform APIs or the Compose testing framework, neither of which is in the current test setup. The established pattern is to **extract pure decision logic out of a ViewModel into a standalone function** (e.g. `RecordingStopDecision.kt`, `GenerateActionDecision.kt`) so the policy is unit-testable even though the ViewModel is not.
