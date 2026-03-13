import sys
from pathlib import Path

# Ensure the src directory is on the import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Temporary output directory for test recordings."""
    return tmp_path / "output"


@pytest.fixture
def sample_config():
    """Minimal valid config dict."""
    return {
        "transcription_provider": "gemini",
        "summarization_provider": "litellm",
        "litellm_transcription_model": "groq/whisper-large-v3",
        "litellm_summarization_model": "gemini/gemini-2.5-flash",
        "api_keys": {},
        "gemini_model": "gemini-flash-latest",
        "whisper_model": "large-v3-turbo",
        "ollama_model": "phi4-mini",
        "ollama_host": "http://localhost:11434",
        "audio_backend": "pulseaudio",
        "screen_recording": False,
        "screen_recorder": "none",
        "monitors": "all",
        "screen_fps": 30,
        "separate_audio_tracks": True,
        "output_folder": "~/meetings",
        "recording_quality": "high",
        "llm_request_timeout_minutes": 5,
        "call_detection_enabled": False,
        "start_at_startup": False,
        "transcription_prompt": "",
        "summarization_prompt": "",
    }
