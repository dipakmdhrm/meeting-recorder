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
- When the meaningful logic is tangled with hard-to-test platform code (Android ViewModels/Compose, GTK UI), **extract the pure logic into a standalone function and test that** — this is the established pattern (e.g. `RecordingStopDecision.kt` + `RecordingStopDecisionTest`, `GenerateActionDecision.kt` + `GenerateActionDecisionTest`).
- Run the relevant suite before opening a PR: `pytest` (Linux) and/or `./gradlew test` (Android).

Skip new tests only when a change genuinely has no testable behavior (docs, comments, pure formatting, trivial constant tweaks) — and say so briefly rather than silently omitting them.

---

## Project Overview

This repository is a monorepo containing two applications: a Linux desktop applet and a native Android app. Both applications record audio, transcribe it, and generate structured notes using Google Gemini. The apps share the same storage format, allowing recordings to be accessed from both platforms.

### Technologies

*   **Linux App:**
    *   **Language:** Python
    *   **UI:** GTK3
    *   **Base dependencies (`linux/requirements.txt`):** `google-genai`, `setproctitle`, `pystray`, `Pillow` — Gemini-only, minimal.
    *   **Opt-in local engines (installed on demand from Settings → Models):** `faster-whisper` (NVIDIA/CPU) installed via pip; `whisper.cpp` built from source with the detected GPU backend (AMD ROCm/Vulkan, Apple Metal, NVIDIA CUDA, or CPU).
    *   **System tray:** left-click focuses the window, right-click opens the menu — uses `Gtk.StatusIcon` where a legacy system tray is present (XFCE/MATE/Cinnamon/KDE-X11/…), else falls back to AppIndicator (menu on any click, e.g. GNOME/Wayland), then pystray. No extra dependency (`Gtk.StatusIcon` ships with GTK3).
*   **Android App:**
    *   **Language:** Kotlin
    *   **UI:** Jetpack Compose
    *   **Dependencies:** `androidx.compose`, `androidx.lifecycle`, `androidx.navigation`, `okhttp`, `coroutines`

### Architecture

The project is structured as a monorepo with two main directories:

*   `linux/`: Contains the source code, tests, and packaging scripts for the Linux desktop app.
*   `android/`: Contains the source code, tests, and Gradle build files for the Android app.

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
