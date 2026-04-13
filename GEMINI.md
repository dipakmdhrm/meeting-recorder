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

## Project Overview

This repository is a monorepo containing two applications: a Linux desktop applet and a native Android app. Both applications record audio, transcribe it, and generate structured notes using Google Gemini. The apps share the same storage format, allowing recordings to be accessed from both platforms.

### Technologies

*   **Linux App:**
    *   **Language:** Python
    *   **UI:** GTK3
    *   **Dependencies:** `google-genai`, `setproctitle`, `pystray`, `Pillow`, `faster-whisper`
*   **Android App:**
    *   **Language:** Kotlin
    *   **UI:** Jetpack Compose
    *   **Dependencies:** `androidx.compose`, `androidx.lifecycle`, `androidx.navigation`, `okhttp`, `coroutines`

### Architecture

The project is structured as a monorepo with two main directories:

*   `linux/`: Contains the source code, tests, and packaging scripts for the Linux desktop app.
*   `android/`: Contains the source code, tests, and Gradle build files for the Android app.

Both apps use Google Gemini for transcription and summarization. The Linux app also supports local transcription with Whisper and local summarization with Ollama.

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
