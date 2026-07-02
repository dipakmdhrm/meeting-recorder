# Audit Roadmap — July 2026

A full architecture audit of both apps (Linux GTK4 applet and Android app) was run on
2026-07-02, producing a 23-item improvement roadmap. A rewrite of the Linux app in another
language was evaluated and rejected: the problems were concentrated in two UI god-objects
and ad-hoc threading (~40% of a small codebase), while the hardest-to-rebuild parts — the
segment-based ffmpeg recorder, the pure-DBus tray, the multi-distro packaging pipeline, and
the pip-based local-engine installs — were exactly the parts that already worked.

Every item shipped as its own reviewable PR (#25–#45), plus a final CI follow-up PR
covering the workflow-file pieces of items 5, 19, and 20.

Legend: **I** = infrastructure, **L** = Linux app, **A** = Android app.

---

## P0 — Ship-blocking bugs

### 1. I1 — Fix .deb dependencies (GTK4) + install smoke test ✅ #25

**Why:** The release workflow generated the Debian package's `Depends:` line inline, and it
still declared the GTK3-era stack — `gir1.2-gtk-3.0`, the Ayatana AppIndicator libraries,
and the `gir1.2-notify-0.7` typelib — even though the app had long since moved to GTK4 +
libadwaita with a pure-DBus tray. The typelibs the app actually imports (`gir1.2-gtk-4.0`,
`gir1.2-adw-1`) were missing entirely, so a fresh `.deb` install on a minimal system could
fail at first launch while pulling in dead GTK3 packages. The bug survived because CI built
its own *fake* control file (`Depends: python3` only) and never exercised the real one, and
because the dependency list was duplicated between two workflow files. `postinst` also
hard-required `gpg` (for the apt-repo key) without declaring it.

**What:** Single `control.template` used by both CI and release workflows; corrected
Depends line (+`gpg`); CI now installs the built .deb in a clean container and imports the
app the way the launcher does, so dependency drift fails the pipeline instead of shipping.

### 2. L5a — Ollama timeouts + error surfacing ✅ #26

**Why:** Pulling a model from Ollama streamed with `timeout=None`, so a stalled local
server hung the worker thread *forever* with no way out. Separately, neither the pull
stream nor the summarization call checked Ollama's `"error"` JSON field — a model-level
failure (model not pulled, out of disk, crashed mid-generation) surfaced as the misleading
"Cannot reach Ollama" (because `HTTPError` subclasses `URLError`) or as a silent pull
failure, sending users debugging their network instead of the actual problem.

**What:** Bounded socket-read timeouts on all streams; `"error"` fields raised with the
server's own message; injectable `http_open` seam so the paths are unit-tested.

### 3. A1 — Android main-thread file I/O ✅ #27

**Why:** `viewModelScope.launch` defaults to the Android main thread, and `MainViewModel`
ran blocking file I/O there at exactly the moments users interact most: creating the
meeting directory on record-start, stat-ing the audio file on stop, writing transcript and
notes on save, walking the entire meeting tree during import, and recursively deleting a
directory on discard. Each was a jank/ANR risk right at the record/save moment. Several
`catch (_: Exception) {}` blocks also swallowed failures invisibly, making field debugging
impossible.

**What:** All file I/O moved under `Dispatchers.IO`; silent catches now log. Behavior and
state-transition ordering unchanged.

### 4. I3 — Fix CLAUDE.md doc drift ✅ #28

**Why:** CLAUDE.md described an audio design that no longer existed: `parec` piping raw PCM
into named pipes with `ffmpeg amix`, and pause via `SIGSTOP`/`SIGCONT`. The real code runs
a single ffmpeg process reading PulseAudio sources directly, with segment-based
pause/resume and concat-on-stop. Because this repo is maintained largely by AI agents that
read CLAUDE.md as ground truth, stale architecture docs don't just mislead — they actively
cause wrong changes. This was the highest-leverage cheap fix in the audit.

**What:** Rewrote the audio-recording section to match `audio/recorder.py`/`mixer.py`;
swept the remaining GTK3-era claims from all agent-facing docs.

---

## P1 — Reliability & security guardrails

### 5. I2 — Lint + type checking in CI (ruff, mypy, ktlint) ✅ #29, #30 + CI follow-up

**Why:** CI ran tests but enforced zero code-quality checks: no Python linter or formatter,
no type checking, no Kotlin linting. Style consistency relied on manual discipline, type
errors could only be found at runtime, and — critically — the large refactors planned in P2
would create new headless modules whose correctness guarantees should come partly from
strict typing. Landing the tooling *before* the refactors meant every extracted module was
born under `mypy --strict`. Adopting it surfaced a real latent bug immediately (the
pipeline accepted `audio_path=None` and passed it into providers).

**What:** ruff (lint + format) repo-wide and `mypy --strict` on the headless Python
packages, wired into CI; ktlint via Gradle with the codebase's IntelliJ style. The
ktlint *CI step* itself landed in the CI follow-up PR.

### 6. L1 — TaskRunner: replace all raw daemon threads ✅ #31

**Why:** The audit's biggest Linux reliability finding. `main_window.py` spawned eight
ad-hoc daemon threads (pipeline runs, recorder stops, cancel flows) that were never joined
— quitting the app killed in-flight work silently. `_Job` objects were mutated from worker
and main threads with no locks (a data race by construction). Worker exceptions could
vanish, and an exception inside a `GLib.idle_add` callback disappeared into GLib. There was
no single place to reason about concurrency at all.

**What:** One app-wide `TaskRunner`: tracked daemon threads, results/errors always routed
to the main thread, unhandled exceptions always logged, bounded-grace shutdown that reports
abandoned tasks, plus a `CancelToken`. Job mutations now happen only on the main thread —
race-free without locks.

### 7. L5 — Pipeline retry, cancellation, key check ✅ #33

**Why:** One transient network failure — a timeout, a connection reset, a 5xx — failed an
entire transcription job, even after a successful upload had already spent the user's
bandwidth and quota. Job "cancellation" was cosmetic: it only stopped UI updates while the
pipeline ran to completion, burning Gemini quota. And a mistyped API key surfaced only as a
failed job at the *end* of a meeting — the most expensive possible moment to learn about a
paste error.

**What:** `retry_on_transient()` (exponential backoff; permanent errors still fail fast)
around all Gemini/Ollama network calls; a `CancelToken` checked between pipeline stages so
cancel actually stops work; a pure API-key format check surfaced as an alert on Settings
save.

### 8. A2 — Android GeminiClient hardening ✅ #42

**Why:** The Android client had the same fragility as the Linux pipeline — zero retry
logic, so a single flaky HTTP roundtrip anywhere in the upload → poll → generate chain
failed the whole flow. The API key was sent as a `?key=` URL query parameter on every
request, leaking it into any URL logging, proxies, or stack traces. And the coroutine
never checked for cancellation between attempts or poll iterations, so a cancelled job
kept uploading and polling.

**What:** Per-step retry with exponential backoff (injectable delay so tests don't sleep);
key moved to the `x-goog-api-key` header everywhere; `ensureActive()` between attempts and
polls.

### 9. L8 — Audio watcher self-healing ✅ #34

**Why:** Call detection worked by spawning `pactl subscribe` exactly once. If that process
died — a PipeWire restart, an audio-server crash, a session hiccup — the watcher went
silently deaf for the rest of the session: no recovery, no restart, and the user simply
stopped getting "call detected" notifications with no indication anything was wrong.

**What:** The watcher loop respawns pactl with exponential backoff (capped, reset after a
healthy run); event matching extracted into a pure, tested helper; injectable
process/clock seams.

### 10. L9 — system_installer security hardening ✅ #32

**Why:** The audit's highest-severity security findings, all in the on-demand installer
paths. `os.system()` ran interpolated shell strings (six sites) — including a Fedora
version read from `rpm` output dropped unvalidated into a command. Ollama was installed via
`curl -fsSL https://ollama.com/install.sh | sh`, the classic pipe-to-shell pattern with
partial-execution hazards and no audit trail. And privilege elevation used bare `sudo` from
a GUI app, which fails silently when there is no terminal to prompt on.

**What:** Argv-list `subprocess` execution everywhere, logged before running; validated
inputs; `pkexec` (polkit auth dialog) with sudo fallback; the Ollama script downloaded over
HTTPS to disk with its SHA-256 logged, then executed — never piped.

---

## P2 — Architecture

### 11. L2 — Extract State machine + Job model ✅ #35

**Why:** The recording lifecycle's legal state transitions existed only implicitly, as
guard clauses scattered across button handlers in a 1,000-line window class; an illegal
jump was undetectable. Job status was tracked with magic strings ("processing"/"done"/
"error"), and the which-buttons-does-a-row-show policy was interleaved with GTK widget
code, making the core domain logic untestable.

**What:** `core/state_machine.py` (pure, exhaustively tested transition table that the
window now validates against) and `core/job.py` (`Job` dataclass, `JobStatus` enum, pure
row-action policy).

### 12. L3 — JobManager with persistence ✅ #36

**Why:** Jobs existed only in window memory. Quit — or crash — while a transcription ran
and the job vanished: the recording stayed on disk, but nothing ever re-offered it, so the
user had to notice the loss and manually re-import the file. The Android app already had
crash recovery (`recoverOrphanedRecordings()`); the Linux app had nothing equivalent.

**What:** `JobManager` persists every job change to `jobs.json` (atomic writes); on startup
interrupted jobs come back as error rows with a Retry button, and finished ones are pruned.

### 13. L4 — Extract RecordingController ✅ #38

**Why:** MainWindow owned the entire recording lifecycle — the Recorder instance, the
stop countdown, cancel/save/discard flows, and the authoritative state — tangled with
widget construction. None of it could be tested without a display, which is precisely why
the recording flows (the app's core feature) had zero test coverage. Device-loss handling
and countdown edge cases (cancel racing a tick) lived untested in UI code.

**What:** Headless `core/recording_controller.py` owning recorder/countdown/state with
injected GTK seams; the window reduced to rendering state changes and forwarding clicks.
The full lifecycle — countdown included — is now unit-tested.

### 14. L6 — JobsPanel + one error-presentation policy ✅ #40

**Why:** The remaining god-object mass in MainWindow was job-row rendering (a widgets-dict
juggling act) and error display. Errors were surfaced inconsistently — some as modal
alerts, some as toasts, some only logged — chosen ad-hoc per call site, so an actionable
problem like a missing API key could scroll by as a transient toast while a routine network
blip interrupted the user with a dialog.

**What:** `ui/jobs_panel.py` renders rows from the pure action policy; `core/errors.py`
holds one tested rule — configuration problems get a modal dialog, runtime failures get a
toast. MainWindow: 1,003 → 791 lines across the L-series, all rendering + forwarding.

### 15. L7 — Split settings dialog into pages ✅ #39

**Why:** `settings_dialog.py` was the second god-object: 972 lines mixing four tabs of
widget construction, service-installer orchestration, background status checks, and the
save flow in one class. Any change to one tab meant navigating (and risking) the other
three; the module was effectively unreviewable as a unit.

**What:** One module per tab under `ui/settings_pages/` (general/models/prompts + shared
widgets); the dialog is a 183-line shell. Injection seams and helper import paths preserved
exactly; behavior-identical.

### 16. A3 — Extract MeetingProcessor ✅ #44

**Why:** The process→save workflow (upload → transcribe → summarize → title → write
transcript.md/notes.md/meeting.json) was duplicated between `MainViewModel` (record flow)
and `MeetingDetailViewModel` (regenerate flows), inside classes that platform APIs make
untestable on the JVM. The app's most valuable logic — the one path every recording goes
through — had no direct test coverage, and fixes had to be applied twice.

**What:** One constructor-injected, JVM-tested `MeetingProcessor` shared by both
ViewModels; a `MeetingStore` interface as the seam for a future SAF storage backend; MIME
mapping deduplicated into a pure `util/Mime.kt`.

### 17. A4 — AppContainer, ViewModel factory, typed routes ✅ #43

**Why:** Every ViewModel obtained its dependencies by casting `application as
MeetingRecorderApp` — an unsafe cast repeated four times that hard-wired ViewModels to the
Application subclass and blocked any form of substitution or testing. Startup orphan
recovery ran on a raw `Thread`. Navigation built route strings by hand with inline `%2F`
escaping at each call site, an invitation for a subtle encoding bug.

**What:** Manual `AppContainer` + a shared `viewModelFactory` (deliberately no Hilt at this
size); constructor-injected ViewModels; coroutine-based orphan recovery; `Routes` helpers
with the path encode/decode extracted pure and JVM-tested.

---

## P3 — Polish & footprint

### 18. L10 — API keys in the Secret Service keyring ✅ #37

**Why:** The Gemini API key sat in plaintext in `config.json`. The chmod-600 permissions
made it acceptable as a floor, but desktop Linux ships a proper secret store (GNOME
Keyring/KWallet) that encrypts at rest, and anything that can read the user's files —
backups, sync tools, a stray `cat` in a screen-share — could exfiltrate the key. Storing
credentials in the platform keyring is the expected behavior for a desktop app.

**What:** `KeyringStore` over the D-Bus Secret Service; config.json carries only a
`@keyring` sentinel; one-time migration of legacy plaintext keys at startup; full graceful
fallback to the old behavior when no keyring exists.

### 19. I4 — Coverage reporting + ARM64 CI smoke ✅ CI follow-up

**Why:** Test coverage was unmeasured — nobody could see which of the newly extracted
modules were actually exercised, or notice coverage regressions. Worse, the `.deb` is
declared `Architecture: all` and the APT repo advertises arm64, but no ARM machine had
ever run the package in CI: the ARM support was a claim, not a tested property.

**What:** `pytest --cov` + Jacoco reports uploaded as CI artifacts on every PR
(report-only, no threshold gate yet), and a QEMU arm64 job that builds the .deb,
installs it in an emulated arm64 container, and imports the app.

### 20. I5 — requirements.lock ✅ #41 + CI follow-up

**Why:** `requirements.txt` used only loose `>=` pins with no lock file, so every user
install resolved dependencies afresh — two installs a week apart could get different
transitive trees, making "works here, fails there" reports undiagnosable and exposing
installs to upstream regressions and supply-chain drift the project never vetted.

**What:** pip-compiled `linux/requirements.lock`; `install.sh` and the `.deb` postinst
prefer it with fallback; the CI and release workflows ship the lock inside the .deb, so
package installs get the pinned tree too.

### 21. — Storage API swap + decision gate documented ✅ #43

**Why:** Two loose ends around Android storage. The Documents path was resolved via the
docs-deprecated `getExternalStoragePublicDirectory()`, a future breakage risk. More
importantly, the audit confirmed `MANAGE_EXTERNAL_STORAGE` is a deliberate trade-off
(GitHub-APK distribution; user-visible, uninstall-surviving folder shared byte-for-byte
with the Linux app) — but that reasoning lived nowhere, so a future contributor (or agent)
could "fix" it and break the app's storage model, or Play Store ambitions could stall on
an undocumented decision.

**What:** Non-deprecated `StorageManager`-based resolution to the same path; the decision
and its Play-Store migration path (SAF tree grant) documented in the architecture docs.

### 22. A5 — org.json → kotlinx.serialization ✅ #45

**Why:** JSON handling used `org.json`'s untyped, mutate-in-place API: no compile-time
schema, field names as scattered string literals, and easy silent drift in the
`meeting.json` format that Android *shares with the Linux app* — where a one-key rename
would corrupt cross-device compatibility. Typed `@Serializable` DTOs turn the wire and
disk formats into checked contracts.

**What:** kotlinx.serialization DTOs for `meeting.json` and the Gemini wire format with
exact field-name preservation; malformed-JSON tolerance kept; tests parse pre-migration
files with org.json as an independent parser to prove byte-level compatibility.

### 23. — Lazy-import google.genai ✅ (no change needed)

**Why:** The `google-genai` SDK is the heaviest Python dependency in the base install, and
importing it at startup would add noticeable cold-start latency and resident memory to a
tray applet that may sit idle all day — the cost would be paid on every launch for a
library only needed when a job actually runs.

**What:** Verified already satisfied: the SDK is imported lazily inside the provider, and
importing the app loads no genai modules. Closed with no change.

---

## Outcome

- **21 PRs merged** (#25–#45), each independently reviewable and CI-gated; releases
  auto-tagged throughout.
- **Tests:** ~180 → ~375 (≈290 Linux + 84 Android JVM); the previously untested recording
  lifecycle, job queue, retry logic, watchers, and processing workflow are all covered.
- **MainWindow 1,003 → 791 lines; settings dialog 972 → 183** — remaining code is widget
  construction and thin forwarding over headless, strictly-typed `core/` modules.
- All 23 items complete.
