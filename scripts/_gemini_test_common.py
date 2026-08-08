"""
Shared helpers for the Gemini pipeline test scripts (``test-*-gemini.py``).

These scripts drive the Linux app's real processing pipeline headlessly so you can compare
Gemini models on your own audio/prompts. This module wires ``linux/src`` onto the import
path, loads the app config (resolving the keyring API key and prompt defaults), creates a
timestamped output directory under ``<repo>/tmp/``, and writes a ``run-info.txt`` summary.

Not a test script itself — it has no ``__main__``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Repo layout: <repo>/scripts/_gemini_test_common.py -> <repo>/linux/src is the package root.
REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = REPO_ROOT / "linux" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _require_google_genai() -> None:
    """Fail early with an actionable hint if the Gemini SDK isn't importable."""
    try:
        import google.genai  # noqa: F401
    except ImportError:
        sys.exit(
            "error: the 'google-genai' package is not installed in this Python.\n"
            "Run these scripts with the app's virtualenv (the one the applet uses), or:\n"
            "    pip install google-genai"
        )


def load_gemini_config(overrides: dict[str, Any]) -> dict[str, Any]:
    """Load the app config, force the Gemini backend, and apply CLI model overrides.

    ``overrides`` values that are ``None`` are ignored (the config/default wins), so a
    caller can pass ``{"gemini_transcription_model": args.model}`` unconditionally.
    """
    _require_google_genai()
    from meeting_recorder.config import settings

    config = settings.load()

    # These are the *Gemini* test scripts — always exercise the Gemini providers,
    # regardless of what the installed app is currently configured to use.
    config["transcription_service"] = "gemini"
    config["summarization_service"] = "gemini"
    # Full-pipeline runs should be exactly transcription + summarization: skip the
    # app's auto-title step, which renames the meeting directory on disk.
    config["auto_title"] = False

    for key, value in overrides.items():
        if value is not None:
            config[key] = value

    if not config.get("gemini_api_key"):
        sys.exit(
            "error: no Gemini API key available.\n"
            "The app stores it in the keyring (config.json holds '@keyring'); make sure the\n"
            "keyring/login session is unlocked, or set gemini_api_key in config.json."
        )

    return config


def make_run_dir() -> Path:
    """Create and return ``<repo>/tmp/test-YYYYMMDD-HHMMSS/`` (anchored to the repo root)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = REPO_ROOT / "tmp" / f"test-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def prompt_source(config: dict[str, Any], key: str) -> str:
    """Report whether a prompt is the user's custom text or the built-in default."""
    return "custom (from config)" if (config.get(key) or "").strip() else "built-in default"


def write_run_info(run_dir: Path, lines: dict[str, Any]) -> None:
    """Write a human-readable ``run-info.txt`` summarising the run."""
    body = "\n".join(f"{label}: {value}" for label, value in lines.items())
    (run_dir / "run-info.txt").write_text(body + "\n", encoding="utf-8")


def status_printer(prefix: str):
    """Return an ``on_status`` callback that prints progress with a stage prefix."""

    def _emit(message: str) -> None:
        print(f"  [{prefix}] {message}", flush=True)

    return _emit
