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
- `CLAUDE.md` — split by scope: this **repo-root** file holds cross-cutting rules; **`linux/CLAUDE.md`** and **`android/CLAUDE.md`** hold each app's architecture, commands, conventions, and test-coverage boundaries. Update the one that owns what you changed (a Linux change → `linux/CLAUDE.md`).
- `GEMINI.md` — the Gemini-facing equivalent, kept as a single repo-root file; mirror the same content changes here.

Before opening a PR, re-read the docs that cover what you touched (README plus the relevant root and/or per-app `CLAUDE.md`, and `GEMINI.md`) and reconcile anything the change made inaccurate (new screens/services, renamed flows, new settings, new tests, changed defaults). Treat doc updates as part of "done," not a follow-up.

---

## Keep tests meaningful — IMPORTANT

For every change, add or update tests when doing so is meaningful — treat it as part of "done," not a follow-up. "Meaningful" means the test would actually catch a regression in the behavior you changed:

- New or changed logic with a testable contract (parsing, decisions, data transforms, repository/IO, API request/response handling) → add or update unit tests that cover the new behavior and its edge cases.
- Fixing a bug → add a test that fails without the fix, so it can't silently regress.
- When the meaningful logic is tangled with hard-to-test platform code (Android ViewModels/Compose, GTK UI), **extract the pure logic into a standalone function and test that** — this is the established pattern (e.g. `RecordingStopDecision.kt` + `RecordingStopDecisionTest`, `GenerateActionDecision.kt` + `GenerateActionDecisionTest`). See the test-coverage boundaries in the per-app `linux/CLAUDE.md` / `android/CLAUDE.md` for what is and isn't unit-tested.
- Run the relevant suite before opening a PR: `pytest` (Linux) and/or `./gradlew test` (Android).

Skip new tests only when a change genuinely has no testable behavior (docs, comments, pure formatting, trivial constant tweaks) — and say so briefly rather than silently omitting them.

---

## Never break user space — IMPORTANT

Backward compatibility is not optional. Every change must satisfy **both** of these:

- **Existing installs keep working.** A user who already has an older version installed must be able to upgrade without their setup breaking — don't invalidate existing config, stored API keys, on-disk recordings/metadata, or packaging state. When a format or default has to change, ship a migration or a compatible fallback rather than a breaking change.
- **Clean installs still work.** The change must also install and run correctly on a fresh system with no prior version present.

If a change genuinely cannot preserve compatibility, call it out explicitly and provide a migration path — never silently break an existing installation.

---

## What this repo is

A monorepo with two independent apps that share the same on-disk recording format (`YYYY/MonthName/DD/HH-MM[_title]/recording.m4a|mp3 + transcript.md + notes.md`):

- `linux/` — GTK4 + libadwaita desktop applet (Python), runs on Debian/Ubuntu/Fedora/Arch
- `android/` — Kotlin/Jetpack Compose app (minSdk 31)

---

## Where the app-specific docs live

Each app's architecture, commands, and test-coverage boundaries live in a `CLAUDE.md` **inside that app's directory**, not here. Claude Code loads a subtree `CLAUDE.md` on demand — only once you read or edit a file in that subtree — so keeping the per-app detail out of this root file means it isn't paid for on every call (e.g. an Android-only session never loads the Linux detail):

- **`linux/CLAUDE.md`** — Linux applet: the daemon/UI split, audio recording, the AI processing pipeline and providers, config/keyring, call detection, GTK4/libadwaita notes, commands, and Linux test-coverage boundaries.
- **`android/CLAUDE.md`** — Android app: DI/navigation, the Gemini client, meeting processing, storage/permissions, commands, and Android test-coverage boundaries.

This root file keeps only the cross-cutting rules that must apply from the first message, before any file is opened. When you change one app's behavior, update **that app's** `CLAUDE.md` (and `GEMINI.md`) in the same PR — see "Keep documentation in sync" above.
