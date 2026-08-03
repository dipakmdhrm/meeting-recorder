# android/CLAUDE.md — Android app

Guidance for the **`android/`** Kotlin/Jetpack Compose app (minSdk 31). Cross-cutting rules — git workflow, "never break user space," the keep-docs/keep-tests policies, and the shared on-disk recording format — live in the repo-root `CLAUDE.md`. This file holds the Android app's architecture, commands, and test-coverage boundaries, and is loaded on demand when you work inside `android/`.

---

## Commands

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

## Android architecture

**Application class & DI:** `MeetingRecorderApp` owns an `AppContainer` (manual dependency container — deliberately no Hilt/Koin at this size) that wires the app-wide singletons: `Config` (SharedPreferences wrapper), `MeetingRepository` (file-system meeting store rooted at `Documents/Meetings/`), a shared `GeminiClient`, and a shared `MeetingProcessor`. ViewModels get these as constructor parameters through the shared `appViewModelFactory` (`viewModelFactory { initializer { … } }` reading the app from `APPLICATION_KEY`) instead of casting `application`; only ViewModels that genuinely need a Context stay `AndroidViewModel` (`MainViewModel` — service start/getString/contentResolver; `MeetingsViewModel` — getString), while `MeetingDetailViewModel` and `SettingsViewModel` are plain `ViewModel`s. Startup orphan recovery (`recoverOrphanedRecordings()`) runs in an application-scoped coroutine (`SupervisorJob() + Dispatchers.IO`), not a raw `Thread`.

**Navigation:** `AppNavGraph` (Compose Navigation) over four screens — `main` (`MainScreen`), `settings` (`SettingsScreen`), `meetings` (`MeetingsScreen`), and `meeting_detail/{meetingPath}` (`MeetingDetailScreen`, Notes/Transcript/Audio tabs). Route strings are defined once in `ui/nav/Routes.kt` (no call site hand-builds them); filesystem paths are passed as nav args with `/` encoded as `%2F` via the pure `Routes.encodeMeetingPath`/`decodeMeetingPath` helpers (JVM-tested in `RoutesTest`).

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

JVM-only unit tests (no Robolectric, no Android platform) in `app/src/test/` cover the testable seams: `Config` invariants; `MeetingRepository` listing/parsing/creating/saving via `TemporaryFolder` — including `meeting.json` backward-compat fixtures and rename-preserves-foreign-keys, with the `org.json` test dependency deliberately kept as an *independent* parser to prove the kotlinx.serialization output stays format-compatible; the `GeminiClient` upload→poll→generate flow (`MockWebServer`); the `MeetingProcessor` process→save workflow; and the extracted pure helpers (`util/Mime.kt`, `RecordingStopDecision`, `GenerateActionDecision`, `Routes` encode/decode).

ViewModels (`MainViewModel`, `MeetingDetailViewModel`, `MeetingsViewModel`, `SettingsViewModel`) and all Compose UI are **not** unit-tested — they need Android platform APIs or the Compose test framework. The established pattern is to **extract pure decision logic out of a ViewModel into a standalone function** (`RecordingStopDecision`, `GenerateActionDecision`) — or a whole workflow into a constructor-injected class (`MeetingProcessor`) — so it's testable even though the ViewModel isn't.
