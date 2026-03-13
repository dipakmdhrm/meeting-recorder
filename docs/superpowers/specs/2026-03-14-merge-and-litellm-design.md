# Merge + LiteLLM Integration — Design Spec

**Date:** 2026-03-14
**Context:** Friend pushed major changes to main (local Whisper STT, Ollama summarization, .deb packaging, settings rewrite). We have the `arch-platform-abstraction` branch (platform abstraction layer, PipeWire, ElevenLabs Scribe v2, Claude Code CLI, gpu-screen-recorder, separate tracks). This spec describes how to merge both sides and add LiteLLM as a unified provider layer.

---

## 1. Merge Strategy

**Approach:** Fresh integration branch from friend's current main. Cherry-pick conflict-free new files from our branch, then manually rewrite the files both sides touched. Add LiteLLM in the same pass.

**Why not rebase:** The settings dialog was rewritten independently on both sides (~700 lines each). A git rebase produces unreadable conflict markers. Building deliberately from friend's main is faster and less error-prone.

---

## 2. Provider Architecture

### 2.1 Transcription Providers (first dropdown)

| Provider | Implementation | Second dropdown |
|----------|---------------|-----------------|
| `gemini` | Direct — native audio upload to multimodal Gemini model | Gemini model list from `defaults.py` |
| `elevenlabs` | Direct — Scribe v2 API with native diarization | None (single model) |
| `whisper` | Direct — local `faster-whisper` in-process (GPU/CPU) | Whisper model list from `defaults.py` |
| `litellm` | `litellm.transcription(model=..., file=...)` | Curated list + free-text ComboBoxEntry |

**Why direct providers stay:**
- **Gemini** — transcribes via multimodal prompt + file upload. LiteLLM doesn't wrap this as a transcription call.
- **ElevenLabs** — LiteLLM only supports `scribe_v1`. We use Scribe v2 with diarization.
- **Whisper** — runs in-process on GPU via `faster-whisper`. Not an HTTP API.

### 2.2 Summarization Providers (first dropdown)

| Provider | Implementation | Second dropdown |
|----------|---------------|-----------------|
| `claude_code` | CLI subprocess — `claude --print` | None (uses user's Claude Code subscription) |
| `litellm` | `litellm.completion(model=..., messages=[...])` | Curated list + free-text ComboBoxEntry |

**What LiteLLM replaces for summarization:**
- Gemini summarization → `litellm.completion("gemini/gemini-2.5-flash", ...)`
- Ollama summarization → `litellm.completion("ollama_chat/phi4-mini", ...)`
- Plus OpenAI, Anthropic, OpenRouter, Groq, Mistral, Cohere, and 100+ others — all via model string prefix.

**What stays direct:**
- Claude Code CLI — uses the user's Claude Code subscription, not the Anthropic API.

### 2.3 LiteLLM Curated Model Lists

Stored in `defaults.py`. Users can pick from these or type any arbitrary `provider/model` string.

**Transcription curated list:**
```python
LITELLM_TRANSCRIPTION_MODELS = [
    "groq/whisper-large-v3",
    "groq/whisper-large-v3-turbo",
    "openai/whisper-1",
    "deepgram/nova-3",
]
```

**Summarization curated list:**
```python
LITELLM_SUMMARIZATION_MODELS = [
    "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-pro",
    "ollama_chat/phi4-mini",
    "ollama_chat/gemma3:4b",
    "ollama_chat/qwen2.5:7b",
    "ollama_chat/llama3.1:8b",
    "anthropic/claude-sonnet-4-20250514",
    "openai/gpt-4o",
    "openrouter/anthropic/claude-sonnet-4",
    "openrouter/openai/gpt-4o",
]
```

### 2.4 LiteLLM Provider Classes

```
src/meeting_recorder/processing/providers/litellm_provider.py
```

Two classes in one file:

**`LiteLLMTranscriptionProvider`**
- Constructor: `(model: str)`
- `transcribe(audio_path, on_status)` → calls `litellm.transcription(model=self._model, file=open(audio_path, "rb"))`
- Returns transcript text

**`LiteLLMSummarizationProvider`**
- Constructor: `(model: str, summarization_prompt: str, timeout_minutes: int)`
- `summarize(transcript, on_status)` → calls `litellm.completion(model=self._model, messages=[...], timeout=...)`
- Returns notes markdown

Both are thin wrappers. The model string is passed straight through to litellm.

### 2.5 Config Keys

```json
{
  "transcription_provider": "gemini",
  "summarization_provider": "litellm",

  "litellm_transcription_model": "groq/whisper-large-v3",
  "litellm_summarization_model": "gemini/gemini-2.5-flash",

  "gemini_model": "gemini-flash-latest",
  "whisper_model": "large-v3-turbo",

  "api_keys": {
    "GEMINI_API_KEY": "...",
    "OPENAI_API_KEY": "..."
  },

  "audio_backend": "pipewire",
  "screen_recording": false,
  "screen_recorder": "none",
  "monitors": "all",
  "screen_fps": 30,
  "separate_audio_tracks": true,
  "capture_mode": "headphones",

  "output_folder": "~/meetings",
  "recording_quality": "high",
  "llm_request_timeout_minutes": 5,
  "call_detection_enabled": false,
  "start_at_startup": false,

  "transcription_prompt": "",
  "summarization_prompt": ""
}
```

### 2.6 Config Migration

`settings.py._migrate_config()` handles:
1. `transcription_service` → `transcription_provider` (if `*_provider` key not already set)
2. `summarization_service` → `summarization_provider`
3. `gemini_api_key` (top-level string) → `api_keys.GEMINI_API_KEY` (move into dict)
4. `elevenlabs_api_key` → `api_keys.ELEVENLABS_API_KEY`
5. `ollama_host` / `ollama_model` — kept as-is (used by Ollama VRAM management and by Models tab for downloading)
6. Old keys removed from top-level after migration

### 2.7 GPU Memory Management

Friend's pipeline.py VRAM orchestration stays:
- Before local Whisper transcription → evict loaded Ollama models from GPU
- After transcription → call `ts_provider.unload()` if available
- After summarization → call `ss_provider.unload()` if available

The provider key reads in pipeline.py updated to use `*_provider` with `*_service` fallback.

---

## 3. API Key Store

### 3.1 Storage

Flat dict in config JSON under `"api_keys"` key. Each entry maps an environment variable name to its value.

### 3.2 Runtime Injection

On app startup (`app.py`), before any provider is initialized:
```python
for env_name, value in cfg.get("api_keys", {}).items():
    if value:
        os.environ[env_name] = value
```

LiteLLM and direct providers auto-read their respective env vars.

### 3.3 Settings UI — "API Keys" Tab

- Scrollable list of rows
- Each row: `[ENV name combo+entry] [key entry (password masked, show/hide toggle)] [delete button]`
- ENV name field is a `Gtk.ComboBoxText` with entry — pre-populated suggestions:
  - `GEMINI_API_KEY`
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `GROQ_API_KEY`
  - `OPENROUTER_API_KEY`
  - `ELEVENLABS_API_KEY`
- User can pick from list or type any custom env var name
- "Add Key" button at the bottom
- **Validation on save:** duplicate env names → error highlight on the duplicate rows, save blocked
- **Security:** config file has `chmod 600`. Key entries use `set_visibility(False)`.

---

## 4. Settings Dialog Structure

Five tabs: **General, Platform, Models, API Keys, Prompts**

### 4.1 General Tab

| Row | Widget | Notes |
|-----|--------|-------|
| Transcription provider | Dropdown: gemini, elevenlabs, whisper, litellm | |
| Transcription model (litellm) | ComboBoxEntry: curated + free-text | Visible only when litellm selected |
| Summarization provider | Dropdown: claude_code, litellm | |
| Summarization model (litellm) | ComboBoxEntry: curated + free-text | Visible only when litellm selected |
| --- separator --- | | |
| Output folder | Entry + Browse button | |
| Recording quality | Dropdown: very_high, high, medium, low | |
| LLM request timeout | Dropdown: 1, 2, 3, 5, 8, 10 min | |
| --- separator --- | | |
| Start at system startup | Switch | |
| Enable call detection | Switch + note text | |

### 4.2 Platform Tab

| Row | Widget | Notes |
|-----|--------|-------|
| Audio backend | Dropdown: pulseaudio, pipewire | |
| Separate audio tracks | Switch | mic + system as independent files |
| --- separator --- | | |
| Screen recording | Switch | |
| Screen recorder | Dropdown: gpu-screen-recorder, none | Visible when screen recording on |
| Monitors | Entry: "all" or comma-separated | Visible when screen recording on |
| FPS | SpinButton: 1-60, default 30 | Visible when screen recording on |

### 4.3 Models Tab (friend's, preserved)

- **Gemini section:** model dropdown from `GEMINI_MODELS`
- **Whisper section:** model dropdown + download grid (model name, size, note, status, download button per model)
- **Ollama section:** model dropdown, host entry, connection status label, download grid

This tab is for managing local model downloads and Gemini model selection. The provider *choice* is in General tab; this tab configures the models *within* each provider.

### 4.4 API Keys Tab (new)

Dynamic key-value list as described in Section 3.3.

### 4.5 Prompts Tab (friend's, preserved)

- Transcription prompt (note: applies to Gemini and LiteLLM LLM-based providers only, not Whisper/ElevenLabs)
- Summarization prompt
- Reset to default buttons per prompt

### 4.6 Button Behavior

- **Save** button — saves config without closing dialog, flashes "Saved!" for 1.2s
- **Close** button — closes dialog without saving (unless already saved)
- Replaces friend's Cancel/OK pattern

---

## 5. Platform Abstraction Layer

Carried over from our branch. No changes to the design.

```
Config → PlatformRegistry (dict lookup) → Concrete Backends
                                            ├── PulseAudio / PipeWire (audio)
                                            ├── gpu-screen-recorder / none (screen)
                                            ├── AppIndicator / pystray (tray)
                                            └── libnotify (notifications)
```

- `app.py` initializes backends from config via `PlatformRegistry`, injects into `MainWindow`
- `MainWindow` accepts `audio_backend` and `screen_recorder` params
- `Recorder` accepts `AudioBackend` ABC via DI, supports `CaptureMode` (headphones/speaker), separate tracks
- Tray delegates to platform-specific backends, icon blinks during recording

---

## 6. File-Level Merge Plan

### 6.1 Conflict-Free — Cherry-pick from our branch

| Files | Description |
|-------|-------------|
| `src/meeting_recorder/platform/**` | Entire platform abstraction directory |
| `src/meeting_recorder/processing/providers/claude_code.py` | Claude Code CLI provider |
| `src/meeting_recorder/processing/providers/elevenlabs.py` | ElevenLabs Scribe v2 provider |
| `tests/**` | All test files + conftest.py |
| `install/install-arch.sh` | Arch Linux install script |
| `requirements-dev.txt` | Dev dependencies (pytest) |
| `docs/superpowers/**` | Plan and spec documents |

### 6.2 New Files to Create

| File | Description |
|------|-------------|
| `src/meeting_recorder/processing/providers/litellm_provider.py` | LiteLLM transcription + summarization providers |

### 6.3 Files to Rewrite (merge both sides + litellm)

| File | What changes |
|------|-------------|
| `config/defaults.py` | Friend's Whisper/Ollama catalogs + our platform keys + litellm curated lists + `TRANSCRIPTION_PROVIDERS`/`SUMMARIZATION_PROVIDERS` lists |
| `config/settings.py` | Friend's base + expanded `_migrate_config()` for `*_service`→`*_provider`, api_keys dict migration, env injection |
| `processing/transcription.py` | Factory with 4 providers: gemini, elevenlabs, whisper, litellm. `*_provider` key with `*_service` fallback |
| `processing/summarization.py` | Factory with 2 providers: claude_code, litellm. Same key fallback |
| `processing/pipeline.py` | Friend's VRAM management + `*_provider` key fallback |
| `ui/settings_dialog.py` | Full merge: friend's Models tab + our Platform tab + new API Keys tab + litellm double-dropdown + Save button |
| `ui/main_window.py` | Our DI changes: accept audio_backend + screen_recorder, AudioResult, screen recorder lifecycle |
| `ui/tray.py` | Our refactor: delegate to platform tray backends |
| `app.py` | Our PlatformRegistry + backend injection + api_keys env injection on startup |
| `README.md` | Combined: both platforms, all providers, litellm usage |
| `install/install-debian.sh` | Friend's install.sh content in our `install/` path structure |
| `requirements.txt` | All deps merged + `litellm` |
| `uninstall.sh` | Our dead code fixes |

### 6.4 Friend's Files — Kept Untouched

| File | Reason |
|------|--------|
| `processing/providers/gemini.py` | No changes needed |
| `processing/providers/whisper.py` | Kept as direct local provider |
| `processing/providers/ollama.py` | Kept for VRAM helpers: `unload_all_models()`, `get_loaded_models()`. `OllamaProvider` class stays but summarization routes through litellm's `ollama_chat/` prefix instead. |
| `.github/workflows/release.yml` | Friend's CI pipeline |
| `packaging/**` | Friend's .deb packaging |

---

## 7. Dependency Changes

### requirements.txt additions
```
litellm
elevenlabs
```

(`faster-whisper` already added by friend)

### install/install-arch.sh additions
```bash
# litellm is installed via pip in the venv (part of requirements.txt)
# No system packages needed for litellm
```

### install/install-debian.sh
Friend's CUDA libs and Ollama install stay. No litellm system deps needed.
