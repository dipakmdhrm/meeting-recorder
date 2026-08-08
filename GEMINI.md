# Meeting Recorder Project (GEMINI.md)

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
4. Wait for CI to pass. If the CI run surfaces failures, or reviewers leave comments,
   validate each against the actual code — reviewers can be stale or wrong. Address the
   valid ones with commits on the same branch; reply to invalid/stale ones explaining why.
   Resolve the review threads you have handled (GraphQL `resolveReviewThread`), don't just
   reply.
5. **Never merge a PR — merging is always the user's decision and action**, even when CI
   is green and all review comments are addressed. Stop when the PR is ready and report
   its URL.
6. After the user merges, releases are tagged from `main` (never from a feature branch);
   the auto-release workflow handles this based on which directories changed
   (v* for Linux, android-* for Android).

**One PR per prompt:** create exactly one pull request per user request, even when the
work is large. Use multiple commits on the same branch for reviewability instead of
fanning out into many small PRs — only split when the user explicitly asks.

This applies to all agents (Claude, Gemini, etc.) — no direct pushes to `main`, and no
merges, under any circumstances.

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
- When the meaningful logic is tangled with hard-to-test platform code (Android ViewModels/Compose, GTK UI), **extract the pure logic into a standalone function and test that** — this is the established pattern (e.g. `RecordingStopDecision.kt` + `RecordingStopDecisionTest`, `GenerateActionDecision.kt` + `GenerateActionDecisionTest`).
- Run the relevant suite before opening a PR: `pytest` (Linux) and/or `./gradlew test` (Android).

Skip new tests only when a change genuinely has no testable behavior (docs, comments, pure formatting, trivial constant tweaks) — and say so briefly rather than silently omitting them.

---

## Never break user space — IMPORTANT

Backward compatibility is not optional. Every change must satisfy **both** of these:

- **Existing installs keep working.** A user who already has an older version installed must be able to upgrade without their setup breaking — don't invalidate existing config, stored API keys, on-disk recordings/metadata, or packaging state. When a format or default has to change, ship a migration or a compatible fallback rather than a breaking change.
- **Clean installs still work.** The change must also install and run correctly on a fresh system with no prior version present.

If a change genuinely cannot preserve compatibility, call it out explicitly and provide a migration path — never silently break an existing installation.

---

## Project Overview

This repository is a monorepo containing two applications: a Linux desktop applet and a native Android app. Both applications record audio, transcribe it, and generate structured notes using Google Gemini. The apps share the same storage format, allowing recordings to be accessed from both platforms.

### Technologies

*   **Linux App:**
    *   **Language:** Python
    *   **UI:** GTK4 + libadwaita (`Adw.Application`/`Adw.ApplicationWindow`, preference-row settings, toasts, dark-mode; async `Gtk.AlertDialog`/`Gtk.FileDialog` instead of blocking `run()`).
    *   **Base dependencies (`linux/requirements.txt`):** `google-genai`, `setproctitle` — Gemini-only, minimal.
    *   **Opt-in local engines (installed on demand from Settings → Models):** `faster-whisper` (NVIDIA/CPU) installed via pip; `whisper.cpp` built from source with the detected GPU backend (AMD ROCm/Vulkan, Apple Metal, NVIDIA CUDA, or CPU).
    *   **System tray:** a pure-DBus StatusNotifierItem (`org.kde.StatusNotifierItem` + `com.canonical.dbusmenu`) built on `Gio.DBusConnection` — no GTK widgets and no extra dependency (Gio ships with PyGObject). Left-click focuses the window where the SNI host delivers `Activate`, otherwise opens the menu. GNOME needs the AppIndicator/KStatusNotifierItem extension to provide the SNI host. The tray shows branded per-state artwork (idle microphone / record-dot / pause / processing) bundled in `assets/tray/` and sent as a raw ARGB `IconPixmap` so it renders on every host and from source.
    *   **App icon:** the launcher/window icon ships in `assets/icons/hicolor/` (scalable SVG + PNG sizes, named `meeting-recorder` — the `Icon=` key) and is installed into the hicolor theme by the install scripts and the Debian/RPM/Arch packaging; `ui/window_app.py:_setup_app_icon()` also registers the bundled tree on the GTK icon-theme search path and sets it as the default icon so it resolves from source. The installed desktop file is named after the application id (`io.github.dipakmdhrm.MeetingRecorder.desktop`, with `StartupWMClass`) so the GNOME/Wayland shell maps a running window to it and shows the app icon rather than a generic one.
*   **Android App:**
    *   **Language:** Kotlin
    *   **UI:** Jetpack Compose
    *   **Dependencies:** `androidx.compose`, `androidx.lifecycle`, `androidx.navigation`, `okhttp`, `coroutines`, `kotlinx-serialization-json` (all JSON handling — meeting.json and the Gemini wire format — uses `kotlinx.serialization` DTOs, not `org.json`)

### Architecture

> **Authoritative architecture documentation lives in `CLAUDE.md`** (Linux architecture,
> Android architecture, and test-coverage boundaries) and is kept in sync with the code
> on every PR. The summary below is intentionally brief — when it disagrees with
> CLAUDE.md, CLAUDE.md is right.

The project is structured as a monorepo with two main directories:

*   `linux/`: Contains the source code, tests, and packaging scripts for the Linux desktop app.
*   `android/`: Contains the source code, tests, and Gradle build files for the Android app.

**Linux runs as two processes (daemon/UI split):** a GTK-free **daemon** (`--daemon`, `daemon/`) owns the recording engine, jobs, pipeline, call detection and tray; the GTK **window** (`--window`, `ui/window_app.py`) is spawned as a child on demand and renders a snapshot fetched over the `io.github.dipakmdhrm.MeetingRecorder.Engine` D-Bus interface. Launching with no flag is **client** mode (`client.py`): ensure the daemon is up, then open a window. `__main__.py` dispatches via `core/run_mode.py`. By default the window hides on close and stays resident for instant reopen (pure policy `core/window_close.py:resolve_close_action`); an opt-in **Low memory mode** setting instead exits the window on close so GTK is loaded only while visible (~20 MB idle in tray vs. ~100 MB). Two more short-lived child roles keep heavy/long work out of the daemon: `--process` (one AI transcription+summarization job) and `--install` (one model/engine install), both spawned and tracked by the daemon and streamed back over D-Bus, so they survive the window closing and don't bloat the daemon. See CLAUDE.md for the full description.

Both apps use Google Gemini for transcription and summarization. The Linux app also supports local transcription with Whisper (`faster-whisper`, NVIDIA/CPU) or whisper.cpp (built from source for AMD/Apple/NVIDIA/CPU GPU acceleration) and local summarization with Ollama. These local engines are not in the base install — they are installed on demand from Settings → Models, keeping a fresh install Gemini-only. The Linux app runs on both x86_64 and arm64.

The Android app records in a foreground service so capture survives interruptions, warns (instead of transcribing) when the OS silences the mic mid-call, and keeps failed/crashed recordings in the library so they can be re-processed. Transcript and notes can be generated, or notes regenerated, directly from a meeting's detail screen, and external audio files can be imported via "Use Existing Recording."

## Building and Running

### Linux App

**Running from Source:**

1.  Create a Python virtual environment:
    ```bash
    python3 -m venv .venv --system-site-packages
    ```
2.  Install dependencies:
    ```bash
    .venv/bin/pip install -r linux/requirements.txt
    ```
3.  Run the application:
    ```bash
    PYTHONPATH=linux/src python3 -m meeting_recorder
    ```

**Running Tests:**

1.  Install pytest:
    ```bash
    pip install pytest
    ```
2.  Run the tests:
    ```bash
    pytest
    ```

### Android App

**Building a Debug APK:**

1.  Navigate to the `android` directory:
    ```bash
    cd android
    ```
2.  Run the `assembleDebug` Gradle task:
    ```bash
    ./gradlew assembleDebug
    ```
    The APK will be located at `android/app/build/outputs/apk/debug/app-debug.apk`.

**Running Unit Tests:**

1.  Navigate to the `android` directory:
    ```bash
    cd android
    ```
2.  Run the `test` Gradle task:
    ```bash
    ./gradlew test
    ```

## Development Conventions

### Continuous Integration

The project uses GitHub Actions for CI. The CI pipeline, defined in `.github/workflows/ci.yml`, runs the following checks on every pull request to the `main` branch:

*   **Unit Tests:** Runs Python unit tests for the Linux app on Python 3.10 and 3.12.
*   **Package Build Smoke Tests:** Builds `.deb`, `.rpm`, and `.pkg.tar.zst` packages for the Linux app to verify the packaging toolchain.
*   **Android Debug Build:** Compiles the Android app and runs its unit tests.

### Release Process

The repository has two release workflows defined in `.github/workflows/`:

*   `release.yml`: Triggered by tags matching `v*` (e.g., `v1.2.0`). It builds and releases the Linux packages to a GitHub Release.
*   `release-android.yml`: Triggered by tags matching `android-*` (e.g., `android-1.0.0`). It builds and releases a signed Android APK to a GitHub Release.
