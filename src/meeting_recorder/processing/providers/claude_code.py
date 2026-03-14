"""Claude Code CLI summarization provider."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Common install locations for claude CLI (desktop launches may have a stripped PATH)
_CLAUDE_SEARCH_PATHS = [
    Path.home() / ".local" / "bin" / "claude",
    Path.home() / ".claude" / "bin" / "claude",
    Path("/usr/local/bin/claude"),
]


def _find_claude() -> str | None:
    """Find the claude CLI binary — checks PATH first, then common locations."""
    found = shutil.which("claude")
    if found:
        return found
    for p in _CLAUDE_SEARCH_PATHS:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


class ClaudeCodeProvider:
    """Summarization via Claude Code CLI (one-off session)."""

    def __init__(self, timeout: int = 300) -> None:
        self._timeout = timeout
        self._claude_path: str | None = None

    def is_available(self) -> bool:
        """Check if `claude` is findable."""
        self._claude_path = _find_claude()
        return self._claude_path is not None

    def summarize(
        self,
        transcript: str,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        """Summarize transcript by shelling out to Claude Code CLI.

        Runs: claude --print --dangerously-skip-permissions -p "<prompt>"
        Pipes transcript via stdin.
        """
        if not self.is_available():
            raise RuntimeError(
                "Claude Code CLI not found. Checked PATH and common locations "
                "(~/.local/bin/claude, ~/.claude/bin/claude)."
            )

        if on_status:
            on_status("Summarizing with Claude Code...")

        prompt = self._build_prompt(transcript)
        claude = self._claude_path

        try:
            result = subprocess.run(
                [
                    claude,
                    "--print",
                    "--dangerously-skip-permissions",
                    "-p", prompt,
                ],
                input=transcript,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"Claude Code exited with code {result.returncode}: {result.stderr}"
                )

            output = result.stdout.strip()
            if not output:
                raise RuntimeError("Claude Code returned empty output")

            return output

        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Claude Code timed out after {self._timeout}s"
            )
        except FileNotFoundError:
            raise RuntimeError("Claude Code CLI (`claude`) not found on PATH")

    def _build_prompt(self, transcript: str) -> str:
        """Build the summarization prompt."""
        from ...config import settings
        from ...config.defaults import SUMMARIZATION_PROMPT
        cfg = settings.load()
        prompt = cfg.get("summarization_prompt", "")
        if not prompt:
            prompt = SUMMARIZATION_PROMPT
        return prompt
