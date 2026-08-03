# linux/CLAUDE.md — Linux desktop app

Guidance for the **`linux/`** GTK4 + libadwaita Python applet. Cross-cutting rules — git workflow, "never break user space," the keep-docs/keep-tests policies, and the shared on-disk recording format — live in the repo-root `CLAUDE.md`. This file holds the Linux app's architecture, commands, and test-coverage boundaries, and is loaded on demand when you work inside `linux/`.

---

## Commands

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

---

## Linux architecture

**Two-process daemon/UI split:** The app runs as a GTK-free **daemon** plus an on-demand GTK **window** child, so GTK/libadwaita is loaded only while a window is open (idle-in-tray footprint drops from ~100 MB to ~20 MB). `__main__.py` dispatches on `core/run_mode.py:resolve_run_mode(argv)`:
- **`--daemon`** (`daemon/app.py:Daemon`): always-on, `Gio`/`GLib` only — no GTK. Runs a GLib main loop and owns the recording lifecycle, job queue, call detection, and the system tray. The engine (`daemon/engine.py:Engine`) is the recording/job logic lifted out of `MainWindow`; it keeps a plain snapshot and fires `on_change`, which the daemon fans out to the tray (`ui/tray.py`, now driven by an `on_command` callback, not a window) and to the D-Bus service. **AI processing does not run in the daemon** — importing the Gemini SDK (`google.genai`) alone costs ~70 MB RSS and Python never unloads a module, so each job runs in a short-lived `--process` **child** (`daemon/processor.py`) that loads the heavy stack, writes transcript.md/notes.md, and exits (daemon idle stays ~40 MB). The child streams `STATUS:`/`RESULT:`/`ERROR:` protocol lines on stdout; `ProcessorLauncher` reads them on the GLib loop and `cancel_job` kills the child. `daemon/engine.py:idle_call` is a lazy `gi` import so the engine is importable headless.
- **`--window`** (`ui/window_app.py:WindowApp`): a short-lived `Adw.Application` (`NON_UNIQUE`, so it doesn't own the bus name) spawned by the daemon as a child. `MainWindow` is now a thin renderer: it fetches a `Snapshot` over D-Bus (`ui/engine_proxy.py:EngineProxy`), re-renders on `SnapshotChanged`/`Error`/`Output` signals, and forwards clicks back as method calls. Closing the window exits the process (GTK memory reclaimed); the daemon keeps recording.
- **no flag** (`client.py`): client mode — ensure the daemon is running (spawn `--daemon` detached if not), then call `OpenWindow`. This is what the app-menu launcher and the tray "Open" invoke.

The daemon↔window boundary is the `io.github.dipakmdhrm.MeetingRecorder.Engine` D-Bus interface (`daemon/dbus_service.py`); the JSON snapshot payload is the pure `core/wire.py` (`Snapshot`/`JobView`). The daemon spawns the window via `Gio.Subprocess` (fork+exec, never a bare fork — it has threads, D-Bus connections and a live ffmpeg child) and supervises a single window via `daemon/window_supervisor.py` (spawn-vs-present). `utils/autostart.py` writes the login entry with `--daemon` and migrates a legacy entry in place on daemon startup, so upgraders get a tray-only login instead of a GTK window (`core/commands.py` holds the shared action vocabulary; `utils/logging_setup.py` the shared logging).

Model/GPU installs (Settings → Models) still run on threads **inside the window process** for now — a **known limitation**: closing the window mid-install aborts it. Moving installs into the daemon (`RunInstall`/`InstallProgress` over D-Bus) is a documented follow-up.

**Audio recording** (`audio/`):
- `recorder.py` runs a single `ffmpeg` subprocess reading PulseAudio/PipeWire sources directly (`-f pulse`); `mixer.py` builds the command — mic+system mode `amerge`s mic (left channel) and sink monitor (right channel) into a true-stereo MP3 with a `highpass=f=80` filter, preserving speaker separation for transcription. Device names are resolved once in `start()` via `devices.py` (`pactl`).
- Pause/resume works via **segments**: pause terminates ffmpeg cleanly (saving the current segment), resume spawns a new ffmpeg writing the next segment, and stop concatenates all segments with ffmpeg's concat demuxer so paused intervals are excluded. `stop()` blocks until ffmpeg exits and segments are merged; a monitor thread reports unexpected ffmpeg death via `on_error`.
- Two modes: mic+system (`Record (Headphones)`) and mic-only (`Record (Speaker)` — the monitor is skipped to avoid echo).

**Recording state machine** (`core/state_machine.py`): `State` (IDLE/RECORDING/PAUSED/COUNTDOWN) plus the pure `can_transition()` legality table — `RecordingController._set_state()` validates against it (logs an error on an illegal jump). `core/job.py` holds the `Job` dataclass (`JobStatus` enum, per-job `CancelToken`) and `actions_for_status()`, the pure policy for which buttons a job row offers. `State` is re-exported from `ui/main_window.py` for existing importers.

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

**UI** (`ui/`): thin renderers over the pure policies above — `main_window.py` (recording controls; job rows built from `actions_for_status()`; errors routed by `core/errors.py:error_presentation()` to a modal dialog vs. a toast), `settings_dialog.py` with per-tab page classes under `settings_pages/` (each exposes `.widget` / `.apply(cfg)`), `meeting_explorer.py` (past-meetings browser), and `tray.py`. The tray is a **pure-DBus StatusNotifierItem** + `com.canonical.dbusmenu` (no GTK widgets, no extra dependency); its icon/menu policy is the gi-free `tray_model`, and it renders the bundled `assets/tray/` PNGs as raw ARGB pixmaps (not a theme `IconName`) so the branded artwork shows on every host. **Gotcha — app-icon resolution:** the installed desktop file is named after `APP_ID` (`io.github.dipakmdhrm.MeetingRecorder.desktop`) with `StartupWMClass`, and `window_app.py:_setup_app_icon()` calls `set_default_icon_name("meeting-recorder")` and adds the bundled `assets/icons/hicolor/` tree to the icon search path — needed so the icon resolves under Wayland/GNOME and when running from source, not only from a packaged install.

**Import convention:** Provider files use 3-dot relative imports (`from ...config.defaults import …`). Files outside `meeting_recorder/` use absolute imports (`from meeting_recorder.config.defaults import …`).

---

## Test coverage boundaries

Headless logic is unit-tested with `pytest` (no GTK or display needed); the GTK layer is not. The rule is to **extract pure decision logic and IO out of GTK code into gi-free helpers/services and test those** — so `core/`, `config/`, `processing/`, `services/`, `detection/`, the headless `daemon/` engine + `core/wire.py` types, and the pure `ui/` policy helpers (`tray_model`, `compute_section_visibility`, `resolve_existing_recording_target`, …) each have tests under `linux/tests/` that mirror the package layout. When you add headless logic, add a test beside it; the payoff of the extract-a-pure-helper pattern is that this is always possible.

**Deliberately not unit-tested** — anything that needs a real display, subprocess, or bus: GTK4 widget construction in `ui/`, async dialog callbacks, the window's D-Bus proxy, the daemon's D-Bus Engine service and GLib main loop, the tray's D-Bus wiring, and the `--process` child entry / `Gio.Subprocess` pipe reading (the engine's *use* of the launcher is tested via an injected fake). If logic you need to change lives here, extract the pure part first and test that.
