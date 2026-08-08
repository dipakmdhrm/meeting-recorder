# Gemini model test scripts

Dev-only helpers to compare Gemini models for meeting **transcription** and
**summarization** on your own audio and your own configured prompts/API key. They drive the
Linux app's *real* processing pipeline headlessly (no GTK, no daemon), so the output matches
what the applet would produce — only the model is swappable per run.

These live outside `linux/` and `android/` on purpose: changes here never trigger an
auto-release.

## Scripts

Run them with the **app's virtualenv Python** so the keyring API key resolves (see
Prerequisites). For a system install that's `/opt/meeting-recorder/venv/bin/python`:

```bash
VENV=/opt/meeting-recorder/venv/bin/python   # the app's venv

# Full pipeline: transcription + summarization
$VENV scripts/test-full-gemini.py --audio /path/audio.mp3 \
    --transcription-model gemini-pro-latest --summarization-model gemini-flash-latest

# Transcription only
$VENV scripts/test-transcription-gemini.py --audio /path/audio.mp3 --model gemini-pro-latest

# Summarization only, from an existing transcript
$VENV scripts/test-summarization-gemini.py --transcript /path/transcript.md --model gemini-flash-latest
```

All model flags are **optional**. When omitted, the model falls back to your
`~/.config/meeting-recorder/config.json` (`gemini_transcription_model` /
`gemini_summarization_model`). Your custom prompts (or the built-in defaults) and your
keyring-stored API key are loaded the same way the app loads them.

Valid model names are the ones listed in the app's Settings → the `GEMINI_MODELS` list in
`linux/src/meeting_recorder/config/defaults.py` (e.g. `gemini-pro-latest`,
`gemini-flash-latest`, `gemini-3.1-pro-preview`, `gemini-2.5-pro`, …).

## Output

Each run creates `tmp/test-<YYYYMMDD-HHMMSS>/` at the repo root containing:

- `transcript.md` and/or `notes.md` — the generated output
- `run-info.txt` — models used, per-run elapsed time, and whether each prompt was your
  custom text or the built-in default (handy for side-by-side model comparisons)

The `tmp/` directory is git-ignored.

## Prerequisites

**Use the app's virtualenv Python** (e.g. `/opt/meeting-recorder/venv/bin/python`). It has
the two things a bare system `python3` usually lacks:

- **`google-genai`** — the Gemini SDK.
- **`secretstorage`** — needed to read your API key from the keyring. The app stores the key
  in the system keyring (config.json holds the `@keyring` sentinel); without `secretstorage`
  the key resolves to empty and the scripts exit with an error.

Also make sure the login keyring is unlocked (open the applet once if unsure). Without a
keyring at all, a plaintext `gemini_api_key` in config.json also works.

The scripts print a clear, actionable error and exit if a prerequisite is missing.
