"""
Tests for the pure Models-tab visibility policy (``compute_section_visibility``).

The widget show/hide is GTK-bound and stays outside unit scope; this guards the
which-sections-and-separators-are-visible decision.
"""

from meeting_recorder.ui.settings_visibility import (
    compute_gemini_row_visibility,
    compute_section_visibility,
    transcription_prompt_note,
)


class TestSectionVisibility:
    def test_default_gemini_only(self):
        vis = compute_section_visibility("gemini", "gemini")
        assert vis["gemini"] is True
        assert vis["whisper"] is False
        assert vis["wcpp"] is False
        assert vis["ollama"] is False
        assert vis["gpu"] is False
        # Gemini is the only visible section → no trailing separators.
        assert not any(v for k, v in vis.items() if k.endswith("_sep"))

    def test_whisper_transcription_shows_gpu(self):
        vis = compute_section_visibility("whisper", "gemini")
        assert vis["gemini"] is True
        assert vis["whisper"] is True
        assert vis["gpu"] is True  # GPU section follows a local STT engine
        assert vis["wcpp"] is False
        assert vis["ollama"] is False

    def test_whisper_cpp_shows_gpu(self):
        vis = compute_section_visibility("whisper_cpp", "gemini")
        assert vis["wcpp"] is True
        assert vis["gpu"] is True
        assert vis["whisper"] is False

    def test_ollama_summarization(self):
        vis = compute_section_visibility("gemini", "ollama")
        assert vis["gemini"] is True
        assert vis["ollama"] is True
        assert vis["gpu"] is False  # Ollama is summarization, not local STT

    def test_separator_only_when_later_section_visible(self):
        # gemini (transcription) + ollama (summarization): gemini and ollama
        # visible, nothing between → gemini_sep True (ollama is later), and
        # whisper/wcpp separators stay False since those sections are hidden.
        vis = compute_section_visibility("gemini", "ollama")
        assert vis["gemini_sep"] is True
        assert vis["whisper_sep"] is False
        assert vis["wcpp_sep"] is False
        assert vis["ollama_sep"] is False  # gpu after it is hidden

    def test_whisper_with_ollama_chain(self):
        # whisper transcription + ollama summarization → gemini hidden,
        # whisper/ollama/gpu visible.
        vis = compute_section_visibility("whisper", "ollama")
        assert vis["gemini"] is False
        assert vis["whisper"] is True
        assert vis["ollama"] is True
        assert vis["gpu"] is True
        # whisper_sep True (ollama/gpu later are visible); ollama_sep True (gpu later).
        assert vis["whisper_sep"] is True
        assert vis["ollama_sep"] is True
        assert vis["gemini_sep"] is False  # gemini section itself hidden

    def test_all_keys_present(self):
        vis = compute_section_visibility("gemini", "gemini")
        for name in ("gemini", "whisper", "wcpp", "ollama", "gpu"):
            assert name in vis
        for name in ("gemini", "whisper", "wcpp", "ollama"):
            assert f"{name}_sep" in vis
        assert "gpu_sep" not in vis  # last section has no trailing separator


class TestGeminiTranscribeService:
    def test_dedicated_stt_service_keeps_the_gemini_section(self):
        # The section holds the API key and the processing timeout, which the
        # speech-to-text model needs just as much as the prompt-based one.
        vis = compute_section_visibility("gemini_transcribe", "gemini")
        assert vis["gemini"] is True
        assert vis["whisper"] is False
        assert vis["wcpp"] is False
        assert vis["gpu"] is False

    def test_section_stays_visible_with_local_summarization(self):
        vis = compute_section_visibility("gemini_transcribe", "ollama")
        assert vis["gemini"] is True
        assert vis["ollama"] is True


class TestGeminiRowVisibility:
    def test_prompt_based_transcription_shows_only_its_own_model(self):
        rows = compute_gemini_row_visibility("gemini", "gemini")
        assert rows["ts_model"] is True
        assert rows["transcribe_model"] is False
        assert rows["transcribe_options"] is False
        assert rows["ss_model"] is True

    def test_dedicated_stt_swaps_in_the_speech_to_text_rows(self):
        rows = compute_gemini_row_visibility("gemini_transcribe", "gemini")
        assert rows["ts_model"] is False
        assert rows["transcribe_model"] is True
        assert rows["transcribe_options"] is True
        assert rows["ss_model"] is True

    def test_local_transcription_hides_both_transcription_models(self):
        rows = compute_gemini_row_visibility("whisper", "gemini")
        assert rows["ts_model"] is False
        assert rows["transcribe_model"] is False
        assert rows["ss_model"] is True

    def test_local_summarization_hides_the_summarization_model(self):
        rows = compute_gemini_row_visibility("gemini", "ollama")
        assert rows["ts_model"] is True
        assert rows["ss_model"] is False


class TestTranscriptionPromptNote:
    def test_dedicated_stt_says_the_prompt_is_unused(self):
        note = transcription_prompt_note("gemini_transcribe")
        assert "takes no prompt" in note

    def test_other_services_keep_the_gemini_only_note(self):
        for service in ("gemini", "whisper", "whisper_cpp"):
            assert "apply to Gemini only" in transcription_prompt_note(service)
