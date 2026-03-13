# Merge + LiteLLM Integration — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the `arch-platform-abstraction` branch with friend's latest main (Whisper, Ollama, .deb packaging) and add LiteLLM as a unified provider layer.

**Architecture:** Fresh integration branch from friend's main. Cherry-pick conflict-free files from our branch, then rewrite the overlapping files to merge both feature sets plus LiteLLM. No backward-compat migration — clean config only.

**Tech Stack:** Python 3.14, GTK3, LiteLLM, ElevenLabs SDK, faster-whisper, PipeWire/PulseAudio, pytest

**Spec:** `docs/superpowers/specs/2026-03-14-merge-and-litellm-design.md`

---

## Chunk 1: Branch Setup + Cherry-Pick Conflict-Free Files

### Task 1: Create integration branch and cherry-pick

**Files:**
- Cherry-pick from `arch-platform-abstraction`: entire `platform/` directory, `tests/`, providers, install scripts, docs

- [ ] **Step 1: Create fresh branch from main**

```bash
git checkout main
git checkout -b integrate-arch-and-litellm
```

- [ ] **Step 2: Cherry-pick new files from our branch using git checkout**

We can't cherry-pick a single commit cleanly (conflicts). Instead, grab individual conflict-free paths:

```bash
# Platform abstraction layer (all new files)
git checkout arch-platform-abstraction -- src/meeting_recorder/platform/

# Our providers (new files, no conflict)
git checkout arch-platform-abstraction -- src/meeting_recorder/processing/providers/claude_code.py
git checkout arch-platform-abstraction -- src/meeting_recorder/processing/providers/elevenlabs.py

# Tests
git checkout arch-platform-abstraction -- tests/

# Arch install script
git checkout arch-platform-abstraction -- install/install-arch.sh

# Dev requirements
git checkout arch-platform-abstraction -- requirements-dev.txt

# Docs
git checkout arch-platform-abstraction -- docs/
```

**Note:** Do NOT cherry-pick `recorder.py` or `tray.py` here — they have incompatible APIs with the current `main_window.py`. They are cherry-picked in Chunk 3 (Task 10) alongside the `main_window.py` rewrite.

Also remove dead code that the arch recorder replaces:
```bash
git rm src/meeting_recorder/audio/devices.py
git rm src/meeting_recorder/audio/mixer.py
```

- [ ] **Step 3: Add .gitignore if missing**

Create or update `.gitignore`:
```
__pycache__/
*.pyc
*.pyo
.pytest_cache/
```

- [ ] **Step 4: Verify cherry-picked files exist**

```bash
ls src/meeting_recorder/platform/audio/base.py
ls src/meeting_recorder/platform/audio/pipewire.py
ls src/meeting_recorder/platform/audio/pulseaudio.py
ls src/meeting_recorder/platform/screen/gpu_screen_recorder.py
ls src/meeting_recorder/platform/tray/appindicator.py
ls src/meeting_recorder/processing/providers/claude_code.py
ls src/meeting_recorder/processing/providers/elevenlabs.py
ls tests/conftest.py
```

- [ ] **Step 5: Commit cherry-picked files**

```bash
git add -A
git commit -m "Cherry-pick platform abstraction, providers, tests from arch branch"
```

---

## Chunk 2: Config + Provider Factories + LiteLLM Provider

### Task 2: Update defaults.py with all config keys

**Files:**
- Modify: `src/meeting_recorder/config/defaults.py`

- [ ] **Step 1: Rewrite defaults.py**

Keep friend's Whisper/Ollama catalogs, add our platform keys, litellm curated lists, and new provider lists. The full `DEFAULT_CONFIG` must include every key from spec Section 2.5.

Key additions to `DEFAULT_CONFIG`:
```python
# Provider selection
"transcription_provider": "gemini",
"summarization_provider": "litellm",

# LiteLLM model strings
"litellm_transcription_model": "groq/whisper-large-v3",
"litellm_summarization_model": "gemini/gemini-2.5-flash",

# API key store
"api_keys": {},

# Platform
"audio_backend": "pipewire",
"screen_recording": False,
"screen_recorder": "none",
"monitors": "all",
"screen_fps": 30,
"separate_audio_tracks": True,
```

New constants to add:
```python
TRANSCRIPTION_PROVIDERS = ["gemini", "elevenlabs", "whisper", "litellm"]
SUMMARIZATION_PROVIDERS = ["claude_code", "litellm"]

LITELLM_TRANSCRIPTION_MODELS = [
    "groq/whisper-large-v3",
    "groq/whisper-large-v3-turbo",
    "openai/whisper-1",
    "deepgram/nova-3",
]

LITELLM_SUMMARIZATION_MODELS = [
    "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-pro",
    "ollama_chat/phi4-mini",
    "ollama_chat/gemma3:4b",
    "ollama_chat/qwen2.5:7b",
    "ollama_chat/llama3.1:8b",
    "anthropic/claude-sonnet-4-latest",
    "openai/gpt-4o",
    "openrouter/anthropic/claude-sonnet-4",
    "openrouter/openai/gpt-4o",
]
```

Remove old `TRANSCRIPTION_SERVICES` / `SUMMARIZATION_SERVICES` constants (replaced by `*_PROVIDERS`).

Remove old `DEFAULT_CONFIG` keys: `transcription_service`, `summarization_service`, `gemini_api_key`.

Keep friend's: `WHISPER_MODELS`, `WHISPER_HF_REPOS`, `WHISPER_MODEL_INFO`, `OLLAMA_MODELS`, `OLLAMA_MODEL_INFO`, `OLLAMA_DEFAULT_HOST`, `GEMINI_MODELS`, `RECORDING_QUALITIES`, `LLM_TIMEOUT_OPTIONS`, prompts.

Unify `llm_request_timeout_minutes` default to `5`.

- [ ] **Step 2: Commit**

```bash
git add src/meeting_recorder/config/defaults.py
git commit -m "Update defaults.py: add provider lists, litellm models, platform keys"
```

### Task 3: Update settings.py with env injection

**Files:**
- Modify: `src/meeting_recorder/config/settings.py`

- [ ] **Step 1: Add inject_api_keys helper**

Add after the existing functions:
```python
def inject_api_keys(config: dict[str, Any] | None = None) -> None:
    """Inject api_keys dict entries into os.environ."""
    if config is None:
        config = load()
    for env_name, value in config.get("api_keys", {}).items():
        if value:
            os.environ[env_name] = value
```

- [ ] **Step 2: Commit**

```bash
git add src/meeting_recorder/config/settings.py
git commit -m "Add inject_api_keys helper to settings.py"
```

### Task 4: Create LiteLLM provider

**Files:**
- Create: `src/meeting_recorder/processing/providers/litellm_provider.py`
- Create: `tests/processing/test_litellm_provider.py`

- [ ] **Step 1: Write tests**

```python
# tests/processing/test_litellm_provider.py
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
import pytest


class TestLiteLLMTranscription:
    @patch("litellm.transcription")
    @patch("builtins.open", mock_open(read_data=b"fake audio"))
    def test_transcribe_calls_litellm(self, mock_transcription):
        from meeting_recorder.processing.providers.litellm_provider import (
            LiteLLMTranscriptionProvider,
        )
        mock_response = MagicMock()
        mock_response.text = "Hello world"
        mock_transcription.return_value = mock_response

        provider = LiteLLMTranscriptionProvider(model="groq/whisper-large-v3")
        result = provider.transcribe(audio_path=Path("/tmp/test.mp3"))

        mock_transcription.assert_called_once()
        assert result == "Hello world"


class TestLiteLLMSummarization:
    @patch("litellm.completion")
    def test_summarize_calls_litellm(self, mock_completion):
        from meeting_recorder.processing.providers.litellm_provider import (
            LiteLLMSummarizationProvider,
        )
        mock_choice = MagicMock()
        mock_choice.message.content = "# Meeting Notes\n- Discussed X"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_completion.return_value = mock_response

        provider = LiteLLMSummarizationProvider(
            model="gemini/gemini-2.5-flash",
            summarization_prompt="Summarize:\n{transcript}",
            timeout_minutes=5,
        )
        result = provider.summarize("Alice said hello. Bob said goodbye.")

        mock_completion.assert_called_once()
        call_args = mock_completion.call_args
        assert "Alice said hello" in call_args.kwargs["messages"][0]["content"]
        assert "Meeting Notes" in result

    @patch("litellm.completion")
    def test_summarize_raises_on_empty(self, mock_completion):
        from meeting_recorder.processing.providers.litellm_provider import (
            LiteLLMSummarizationProvider,
        )
        mock_choice = MagicMock()
        mock_choice.message.content = ""
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_completion.return_value = mock_response

        provider = LiteLLMSummarizationProvider(
            model="gemini/gemini-2.5-flash",
            summarization_prompt="Summarize:\n{transcript}",
            timeout_minutes=5,
        )
        with pytest.raises(RuntimeError):
            provider.summarize("test transcript")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/processing/test_litellm_provider.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: Write implementation**

```python
# src/meeting_recorder/processing/providers/litellm_provider.py
"""LiteLLM-based providers for transcription and summarization."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class LiteLLMTranscriptionProvider:
    """Transcribes audio via litellm.transcription() — supports Groq Whisper, OpenAI Whisper, Deepgram, etc."""

    def __init__(self, model: str) -> None:
        self._model = model

    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        import litellm

        if on_status:
            on_status(f"Transcribing with {self._model}…")

        with open(audio_path, "rb") as f:
            response = litellm.transcription(model=self._model, file=f)

        return response.text


class LiteLLMSummarizationProvider:
    """Summarizes transcripts via litellm.completion() — supports 100+ LLM providers."""

    def __init__(
        self,
        model: str,
        summarization_prompt: str = "",
        timeout_minutes: int = 5,
    ) -> None:
        self._model = model
        self._prompt = summarization_prompt
        self._timeout = timeout_minutes * 60

    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        import litellm

        if on_status:
            on_status(f"Summarizing with {self._model}…")

        prompt = self._prompt
        try:
            prompt = prompt.format(transcript=transcript)
        except (KeyError, IndexError):
            prompt = prompt + f"\n\nTRANSCRIPT:\n{transcript}"

        response = litellm.completion(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            timeout=self._timeout,
        )

        text = response.choices[0].message.content.strip()
        if not text:
            raise RuntimeError(
                f"LiteLLM returned empty response for model {self._model!r}"
            )
        return text
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/processing/test_litellm_provider.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/meeting_recorder/processing/providers/litellm_provider.py tests/processing/test_litellm_provider.py
git commit -m "Add LiteLLM transcription + summarization providers"
```

### Task 5: Rewrite transcription.py factory

**Files:**
- Modify: `src/meeting_recorder/processing/transcription.py`

- [ ] **Step 1: Rewrite factory with 4 providers**

```python
"""Provider factory for transcription."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        on_status: Callable[[str], None] | None = None,
    ) -> str: ...


def create_transcription_provider(config: dict) -> TranscriptionProvider:
    """Factory: return the configured transcription provider."""
    provider = config.get("transcription_provider", "gemini")

    if provider == "gemini":
        from .providers.gemini import GeminiProvider
        return GeminiProvider(
            api_key=os.environ.get("GEMINI_API_KEY", ""),
            model=config.get("gemini_model", "gemini-2.5-flash"),
            transcription_prompt=config.get("transcription_prompt", ""),
            timeout_minutes=config.get("llm_request_timeout_minutes", 5),
        )

    if provider == "elevenlabs":
        from .providers.elevenlabs import ElevenLabsProvider
        return ElevenLabsProvider(
            api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
        )

    if provider == "whisper":
        from .providers.whisper import WhisperProvider
        return WhisperProvider(
            model=config.get("whisper_model", "large-v3-turbo"),
        )

    if provider == "litellm":
        from .providers.litellm_provider import LiteLLMTranscriptionProvider
        return LiteLLMTranscriptionProvider(
            model=config.get("litellm_transcription_model", "groq/whisper-large-v3"),
        )

    raise ValueError(f"Unknown transcription provider: {provider!r}")
```

- [ ] **Step 2: Commit**

```bash
git add src/meeting_recorder/processing/transcription.py
git commit -m "Rewrite transcription factory: gemini, elevenlabs, whisper, litellm"
```

### Task 6: Rewrite summarization.py factory

**Files:**
- Modify: `src/meeting_recorder/processing/summarization.py`

- [ ] **Step 1: Rewrite factory with 2 providers**

```python
"""Provider factory for summarization."""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class SummarizationProvider(Protocol):
    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str: ...


def create_summarization_provider(config: dict) -> SummarizationProvider:
    """Factory: return the configured summarization provider."""
    provider = config.get("summarization_provider", "litellm")

    if provider == "claude_code":
        from .providers.claude_code import ClaudeCodeProvider
        return ClaudeCodeProvider(
            timeout=config.get("llm_request_timeout_minutes", 5) * 60,
        )

    if provider == "litellm":
        from .providers.litellm_provider import LiteLLMSummarizationProvider
        return LiteLLMSummarizationProvider(
            model=config.get("litellm_summarization_model", "gemini/gemini-2.5-flash"),
            summarization_prompt=config.get("summarization_prompt", ""),
            timeout_minutes=config.get("llm_request_timeout_minutes", 5),
        )

    raise ValueError(f"Unknown summarization provider: {provider!r}")
```

- [ ] **Step 2: Commit**

```bash
git add src/meeting_recorder/processing/summarization.py
git commit -m "Rewrite summarization factory: claude_code, litellm"
```

### Task 7: Update pipeline.py to use new config keys

**Files:**
- Modify: `src/meeting_recorder/processing/pipeline.py`

- [ ] **Step 1: Update key names in _run_separate**

Change all `transcription_service` → `transcription_provider`, `summarization_service` → `summarization_provider`.

The VRAM management logic stays the same, just key names change:
```python
ts_provider_name = self._config.get("transcription_provider", "gemini")
# ...
if ts_provider_name == "whisper":
    # evict ollama models from VRAM
```

- [ ] **Step 2: Commit**

```bash
git add src/meeting_recorder/processing/pipeline.py
git commit -m "Update pipeline.py: use transcription_provider/summarization_provider keys"
```

### Task 8: Update requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add litellm and elevenlabs**

Final requirements.txt:
```
google-genai>=0.8.0
pystray>=0.19.0
Pillow>=10.0.0
faster-whisper>=1.1.0
litellm
elevenlabs>=1.0.0
```

- [ ] **Step 2: Commit**

```bash
git add requirements.txt
git commit -m "Add litellm and elevenlabs to requirements"
```

**Note:** Do NOT run the full test suite here — `recorder.py` and `tray.py` haven't been updated yet (that's Chunk 3). Only the litellm tests from Task 4 should pass at this point.

---

## Chunk 3: App Integration (app.py, main_window.py, recorder.py, tray.py)

### Task 9: Rewrite app.py with platform registry + env injection

**Files:**
- Modify: `src/meeting_recorder/app.py`

- [ ] **Step 1: Add PlatformRegistry + backend injection + api_keys env injection**

In `_create_window()`:
```python
def _create_window(self) -> None:
    from .ui.main_window import MainWindow
    from .platform.registry import PlatformRegistry
    from .config.settings import inject_api_keys

    cfg = settings.load()
    inject_api_keys(cfg)

    registry = PlatformRegistry()

    # Audio backend
    audio_backend_name = cfg.get("audio_backend", "pulseaudio")
    audio_backend_cls = registry.get_audio_backend(audio_backend_name)
    if audio_backend_cls is None:
        available = registry.available_audio_backends()
        if available:
            audio_backend_cls = registry.get_audio_backend(available[0])
    audio_backend = audio_backend_cls() if audio_backend_cls else None

    # Screen recorder
    screen_recorder = None
    if cfg.get("screen_recording"):
        sr_name = cfg.get("screen_recorder", "none")
        sr_cls = registry.get_screen_recorder(sr_name)
        if sr_cls:
            screen_recorder = sr_cls()

    self.window = MainWindow(
        application=self,
        audio_backend=audio_backend,
        screen_recorder=screen_recorder,
    )
    # ... rest stays the same (tray, call detector, show_all, validate)
```

- [ ] **Step 2: Commit**

```bash
git add src/meeting_recorder/app.py
git commit -m "Wire up PlatformRegistry, backend injection, api_keys env injection"
```

### Task 10: Cherry-pick recorder + tray, then update main_window.py

**Files:**
- Cherry-pick: `src/meeting_recorder/audio/recorder.py`, `src/meeting_recorder/ui/tray.py`
- Modify: `src/meeting_recorder/ui/main_window.py`

This is the largest single change. The recorder and tray are cherry-picked here (not in Chunk 1) because they have API changes that require main_window.py to be updated in the same step.

- [ ] **Step 1: Cherry-pick recorder and tray from arch branch**

```bash
git checkout arch-platform-abstraction -- src/meeting_recorder/audio/recorder.py
git checkout arch-platform-abstraction -- src/meeting_recorder/ui/tray.py
```

- [ ] **Step 2: Update __init__ to accept backends**

Add `audio_backend=None, screen_recorder=None` params to `__init__`. Store as `self._audio_backend`, `self._screen_recorder`.

- [ ] **Step 2: Update _start_recording to use audio_backend.validate()**

Replace hardcoded pactl checks with `self._audio_backend.validate()`. Create `Recorder(backend=self._audio_backend, ...)` instead of the old constructor. Start screen recorder after audio.

- [ ] **Step 3: Update stop/cancel methods to stop screen recorder**

`_stop_recorder_bg`, `on_cancel_save_clicked`, `on_cancel_clicked` — stop screen recorder before audio.

- [ ] **Step 5: Update _check_api_keys to use os.environ**

Check both transcription and summarization providers. For litellm, extract the provider prefix from the model string and check the corresponding env var.

```python
# Known litellm provider prefixes → env var names
_LITELLM_KEY_MAP = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
}

def _check_api_keys(self) -> str | None:
    cfg = settings.load()
    # Check transcription provider
    ts = cfg.get("transcription_provider", "gemini")
    if ts == "gemini" and not os.environ.get("GEMINI_API_KEY"):
        return "Gemini API key not set (add in Settings → API Keys)"
    if ts == "elevenlabs" and not os.environ.get("ELEVENLABS_API_KEY"):
        return "ElevenLabs API key not set (add in Settings → API Keys)"
    if ts == "litellm":
        model = cfg.get("litellm_transcription_model", "")
        prefix = model.split("/")[0] if "/" in model else ""
        env_key = _LITELLM_KEY_MAP.get(prefix)
        if env_key and not os.environ.get(env_key):
            return f"{env_key} not set for {model} (add in Settings → API Keys)"
    # Check summarization provider
    ss = cfg.get("summarization_provider", "litellm")
    if ss == "litellm":
        model = cfg.get("litellm_summarization_model", "")
        prefix = model.split("/")[0] if "/" in model else ""
        env_key = _LITELLM_KEY_MAP.get(prefix)
        if env_key and not os.environ.get(env_key):
            return f"{env_key} not set for {model} (add in Settings → API Keys)"
    return None
```

- [ ] **Step 6: Update config key reads**

All reads of `transcription_service` → `transcription_provider`, `summarization_service` → `summarization_provider`, `gemini_api_key` → `os.environ.get("GEMINI_API_KEY")`.

- [ ] **Step 7: Commit**

```bash
git add src/meeting_recorder/audio/recorder.py src/meeting_recorder/ui/tray.py src/meeting_recorder/ui/main_window.py
git commit -m "Cherry-pick recorder+tray, rewrite MainWindow: DI, screen recorder, API key checks"
```

- [ ] **Step 8: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: All tests pass. Fix any failures from config key renames in test fixtures (`transcription_service` → `transcription_provider`, etc.).

---

## Chunk 4: Settings Dialog

### Task 11: Rewrite settings_dialog.py

**Files:**
- Modify: `src/meeting_recorder/ui/settings_dialog.py`

This is the most complex file. Build it using friend's structure as base, adding our tabs and features.

- [ ] **Step 1: Write the full settings dialog**

Five tabs: General, Platform, Models, API Keys, Prompts.

Key structural changes from friend's version:
1. **Buttons:** Replace Cancel/OK with Close (`Gtk.ResponseType.CLOSE`) + Save (`Gtk.ResponseType.APPLY`). Save uses `stop_emission_by_name("response")` to stay open.
2. **General tab:** Add transcription_provider and summarization_provider dropdowns (from `TRANSCRIPTION_PROVIDERS` / `SUMMARIZATION_PROVIDERS`). When "litellm" is selected, show a `Gtk.ComboBoxText` with entry populated from `LITELLM_*_MODELS`. Connect `changed` signal to show/hide the model row.
3. **Platform tab (new):** Audio backend dropdown, separate tracks switch, screen recording switch with conditional screen recorder/monitors/fps rows.
4. **API Keys tab (new):** Scrollable `Gtk.ListBox` or `Gtk.Box` of rows. Each row: `ComboBoxText` with entry for env name (pre-populated suggestions), password entry, delete button. "Add Key" button at bottom. Validation for duplicates on save.
5. **Models tab:** Keep friend's Gemini/Whisper/Ollama sections with download grids verbatim.
6. **Prompts tab:** Keep friend's version verbatim.
7. **`_save()`:** Write all new config keys: `transcription_provider`, `summarization_provider`, `litellm_transcription_model`, `litellm_summarization_model`, `api_keys` dict, `audio_backend`, `screen_recording`, `screen_recorder`, `monitors`, `screen_fps`, `separate_audio_tracks`.
8. **Flash feedback:** `_flash_saved()` sets button label to "Saved!", disables for 1.2s via `GLib.timeout_add`.
9. **Models tab Gemini section:** Remove the `gemini_api_key` entry (moved to API Keys tab). Keep model dropdown and timeout. Ensure timeout fallback is `5` (not `3`).
10. **API Keys tab duplicate validation:** On save, scan all rows for duplicate env names. If found, add CSS class `error` to the duplicate rows and block save with an inline warning label.

Import additions:
```python
from ..config.defaults import (
    # ... existing ...
    TRANSCRIPTION_PROVIDERS,
    SUMMARIZATION_PROVIDERS,
    LITELLM_TRANSCRIPTION_MODELS,
    LITELLM_SUMMARIZATION_MODELS,
)
```

The `_SERVICE_LABELS` dict needs entries for all providers:
```python
_PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "elevenlabs": "ElevenLabs Scribe v2",
    "whisper": "Whisper (local)",
    "litellm": "LiteLLM (100+ providers)",
    "claude_code": "Claude Code CLI",
}
```

Pre-populated env var suggestions for API Keys tab:
```python
_SUGGESTED_ENV_KEYS = [
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "ELEVENLABS_API_KEY",
    "DEEPGRAM_API_KEY",
]
```

- [ ] **Step 2: Commit**

```bash
git add src/meeting_recorder/ui/settings_dialog.py
git commit -m "Rewrite settings dialog: 5 tabs, litellm double-dropdown, API key store, Save button"
```

---

## Chunk 5: Install Scripts, README, Cleanup

### Task 12: Update install/install-debian.sh

**Files:**
- Modify: `install/install-debian.sh`

- [ ] **Step 1: Copy friend's install.sh to install/install-debian.sh**

Take friend's current `install.sh` content (with CUDA, Ollama) and place it in `install/install-debian.sh`. Fix paths to use `REPO_DIR="$SCRIPT_DIR/.."` since the script now lives in `install/` subdirectory. Update the default config JSON written by the script to use new key names (`transcription_provider`, `summarization_provider`, `api_keys`).

- [ ] **Step 2: Commit**

```bash
git add install/install-debian.sh
git commit -m "Update install-debian.sh: new config keys, fixed paths"
```

### Task 13: Update install/install-arch.sh default config

**Files:**
- Modify: `install/install-arch.sh`

- [ ] **Step 1: Update the default config.json in the script**

Update the config JSON block to use new key names matching `DEFAULT_CONFIG`.

- [ ] **Step 2: Commit**

```bash
git add install/install-arch.sh
git commit -m "Update install-arch.sh: new config key names"
```

### Task 14: Update uninstall.sh

**Files:**
- Modify: `uninstall.sh`

- [ ] **Step 1: Remove dead /var/log/meeting-recorder step, fix paths**

- [ ] **Step 2: Commit**

```bash
git add uninstall.sh
git commit -m "Fix uninstall.sh: remove dead log dir step"
```

### Task 15: Rewrite README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write combined README**

Cover:
- Both platforms (Debian + Arch)
- All transcription providers (Gemini, ElevenLabs, Whisper, LiteLLM)
- All summarization providers (Claude Code, LiteLLM → Gemini/Ollama/OpenAI/etc.)
- LiteLLM usage and model strings
- Platform settings (PipeWire, screen recording)
- API key configuration
- Install instructions for both platforms
- .deb package install (friend's APT repo)

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Rewrite README: multi-platform, all providers, litellm"
```

---

## Chunk 6: Tests + Verification

### Task 16: Fix existing tests for new config keys

**Files:**
- Modify: `tests/` (various files that use old config key names)

- [ ] **Step 1: Update test fixtures**

Any test that creates config dicts with `transcription_service` → change to `transcription_provider`. Same for `summarization_service`, `gemini_api_key` → use env vars or `api_keys` dict.

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: All tests pass

- [ ] **Step 3: Commit any test fixes**

```bash
git add tests/
git commit -m "Fix tests for new config key names"
```

### Task 17: Manual verification

- [ ] **Step 1: Install and launch**

```bash
# Copy source to install dir
rm -rf ~/.local/share/meeting-recorder/src
cp -r src ~/.local/share/meeting-recorder/src

# Launch
meeting-recorder
```

- [ ] **Step 2: Verify settings dialog**

- All 5 tabs render
- General tab: transcription/summarization dropdowns work, litellm shows model ComboBoxEntry
- Platform tab: audio backend, screen recording options
- Models tab: Gemini/Whisper/Ollama sections with download grids
- API Keys tab: can add/remove keys, duplicate validation works
- Prompts tab: prompt editing works
- Save button saves without closing, shows "Saved!" flash

- [ ] **Step 3: Verify recording works**

- Start a recording → transcription → summarization pipeline completes

- [ ] **Step 4: Final commit if any fixes needed**
