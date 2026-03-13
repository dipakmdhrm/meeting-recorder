from unittest.mock import patch, MagicMock
import subprocess
import pytest

from meeting_recorder.processing.providers.claude_code import ClaudeCodeProvider


@pytest.fixture
def provider():
    return ClaudeCodeProvider()


def test_provider_has_summarize_method(provider):
    assert callable(getattr(provider, "summarize", None))


@patch("meeting_recorder.processing.providers.claude_code.shutil.which")
def test_is_available_true(mock_which, provider):
    mock_which.return_value = "/usr/local/bin/claude"
    assert provider.is_available() is True


@patch("meeting_recorder.processing.providers.claude_code._CLAUDE_SEARCH_PATHS", [])
@patch("meeting_recorder.processing.providers.claude_code.shutil.which")
def test_is_available_false(mock_which, provider):
    mock_which.return_value = None
    assert provider.is_available() is False


@patch("meeting_recorder.config.settings.load", return_value={"summarization_prompt": ""})
@patch("meeting_recorder.processing.providers.claude_code.subprocess.run")
@patch("meeting_recorder.processing.providers.claude_code.shutil.which")
def test_summarize_calls_claude_cli(mock_which, mock_run, mock_load, provider):
    mock_which.return_value = "/usr/local/bin/claude"
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="## Meeting Notes\n\n- Point 1", stderr=""
    )

    result = provider.summarize("transcript text here")

    assert "Meeting Notes" in result
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "--print" in call_args


@patch("meeting_recorder.config.settings.load", return_value={"summarization_prompt": ""})
@patch("meeting_recorder.processing.providers.claude_code.subprocess.run")
@patch("meeting_recorder.processing.providers.claude_code.shutil.which")
def test_summarize_raises_on_failure(mock_which, mock_run, mock_load, provider):
    mock_which.return_value = "/usr/local/bin/claude"
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="error"
    )

    with pytest.raises(RuntimeError, match="Claude Code"):
        provider.summarize("transcript")
