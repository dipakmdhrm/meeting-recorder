"""
Tests for WhisperStatusChecker and WhisperDownloader.

WhisperStatusChecker is tested with a tmp_path so no real HuggingFace cache
is touched.  WhisperDownloader is tested with a no-op loader so no model
weights are downloaded.
"""
import pytest
from pathlib import Path
from meeting_recorder.services.whisper_service import WhisperDownloader, WhisperStatusChecker


# ── WhisperStatusChecker ──────────────────────────────────────────────────────

class TestWhisperStatusCheckerIsCached:
    def test_true_when_cache_directory_exists(self, tmp_path):
        cache_root = tmp_path / "hub"
        (cache_root / "models--Systran--faster-whisper-small").mkdir(parents=True)
        checker = WhisperStatusChecker(cache_root=cache_root)
        assert checker.is_cached("small") is True

    def test_false_when_cache_directory_missing(self, tmp_path):
        checker = WhisperStatusChecker(cache_root=tmp_path / "hub")
        assert checker.is_cached("small") is False

    def test_uses_hf_repo_mapping_for_known_model(self, tmp_path):
        # "distil-large-v3" → "Systran/faster-distil-whisper-large-v3"
        cache_root = tmp_path / "hub"
        (cache_root / "models--Systran--faster-distil-whisper-large-v3").mkdir(parents=True)
        checker = WhisperStatusChecker(cache_root=cache_root)
        assert checker.is_cached("distil-large-v3") is True

    def test_uses_fallback_path_for_unknown_model(self, tmp_path):
        # Unknown model falls back to "Systran/faster-whisper-{model}"
        cache_root = tmp_path / "hub"
        (cache_root / "models--Systran--faster-whisper-custom").mkdir(parents=True)
        checker = WhisperStatusChecker(cache_root=cache_root)
        assert checker.is_cached("custom") is True

    def test_known_model_does_not_match_wrong_directory(self, tmp_path):
        # Ensure the correct repo slug is used, not the fallback slug
        cache_root = tmp_path / "hub"
        # Create only the fallback path, not the correct mapped path
        (cache_root / "models--Systran--faster-whisper-distil-large-v3").mkdir(parents=True)
        checker = WhisperStatusChecker(cache_root=cache_root)
        # "distil-large-v3" should look for the mapped repo, not the fallback
        assert checker.is_cached("distil-large-v3") is False


# ── WhisperDownloader ─────────────────────────────────────────────────────────

class TestWhisperDownloader:
    def test_calls_loader_with_model_name(self):
        loaded: list[str] = []
        downloader = WhisperDownloader(model_loader=lambda m: loaded.append(m))
        downloader.download("small")
        assert loaded == ["small"]

    def test_propagates_exception_from_loader(self):
        def boom(m): raise RuntimeError("disk full")
        downloader = WhisperDownloader(model_loader=boom)
        with pytest.raises(RuntimeError, match="disk full"):
            downloader.download("small")
