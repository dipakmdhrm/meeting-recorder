"""
Manages persistent application configuration. It provides functions to load and save user settings
from a JSON file, ensuring that sensitive data like API keys are stored with restricted file
permissions and merged with default values.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

from .defaults import CONFIG_DIR, CONFIG_FILE, DEFAULT_CONFIG
from .keyring_store import KeyringStore

logger = logging.getLogger(__name__)

# Written to config.json in place of the real key when it lives in the
# Secret Service keyring. Deliberately not a plausible key value.
KEYRING_SENTINEL = "@keyring"

_keyring_store: KeyringStore | None = None


def _get_keyring() -> KeyringStore:
    global _keyring_store
    if _keyring_store is None:
        _keyring_store = KeyringStore()
    return _keyring_store


def _config_path() -> Path:
    return Path(os.path.expanduser(CONFIG_FILE))


def _config_dir() -> Path:
    return Path(os.path.expanduser(CONFIG_DIR))


def load(keyring: KeyringStore | None = None) -> dict[str, Any]:
    """Load config, returning defaults merged with stored values.

    A ``@keyring`` sentinel in gemini_api_key is resolved from the Secret
    Service; if the keyring is unreachable the key comes back empty (the UI
    then prompts for it) rather than crashing.
    """
    path = _config_path()
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            with open(path) as f:
                stored = json.load(f)

            # Migration: if old gemini_model exists, copy it to the new keys
            if "gemini_model" in stored:
                stored.setdefault("gemini_transcription_model", stored["gemini_model"])
                stored.setdefault("gemini_summarization_model", stored["gemini_model"])

            # Merge: stored values override defaults, unknown keys ignored
            for key in DEFAULT_CONFIG:
                if key in stored:
                    config[key] = stored[key]
        except Exception as exc:
            logger.warning("Failed to load config: %s", exc)

    if config.get("gemini_api_key") == KEYRING_SENTINEL:
        store = keyring or _get_keyring()
        config["gemini_api_key"] = store.get() or ""
    return config


def save(config: dict[str, Any], keyring: KeyringStore | None = None) -> None:
    """Save config with 600 permissions; API key goes to the keyring when possible.

    When the Secret Service is available the real key is stored there and
    config.json only carries the ``@keyring`` sentinel; otherwise the key is
    written to the chmod-600 file as before.
    """
    store = keyring or _get_keyring()
    to_write = dict(config)
    key = str(to_write.get("gemini_api_key") or "")
    if key and key != KEYRING_SENTINEL:
        if store.available() and store.set(key):
            to_write["gemini_api_key"] = KEYRING_SENTINEL
    elif not key:
        # Key cleared — don't leave a stale secret behind in the keyring.
        store.delete()
    _write(to_write)


def migrate_key_to_keyring(keyring: KeyringStore | None = None) -> bool:
    """One-time startup migration: move a plaintext key into the keyring.

    Returns True if a key was moved. No-op (False) when there is no plaintext
    key, or the keyring is unavailable — plaintext chmod-600 storage then
    remains in effect.
    """
    path = _config_path()
    if not path.exists():
        return False
    try:
        stored = json.loads(path.read_text())
    except Exception:
        return False
    key = str(stored.get("gemini_api_key") or "")
    if not key or key == KEYRING_SENTINEL:
        return False
    store = keyring or _get_keyring()
    if not (store.available() and store.set(key)):
        return False
    stored["gemini_api_key"] = KEYRING_SENTINEL
    _write(stored)
    logger.info("Migrated Gemini API key from config.json into the Secret Service keyring")
    return True


def _write(config: dict[str, Any]) -> None:
    """Atomically write config.json with 600 permissions."""
    d = _config_dir()
    d.mkdir(parents=True, exist_ok=True)

    path = _config_path()
    # Write to a temp file first so a crash or disk-full error never leaves a
    # half-written (and therefore unparseable) config.json.
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
        # Lock down permissions before the rename so there is no window where the
        # file is world-readable. The config may store API keys in plaintext
        # when no keyring is available.
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.rename(path)
        # rename() preserves permissions on Linux, but set them again to be safe
        # (e.g. if the file already existed with looser permissions).
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception as exc:
        logger.error("Failed to save config: %s", exc)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def get(key: str, default: Any = None) -> Any:
    """Convenience: load config and return a single key."""
    return load().get(key, default)


def api_key_error(config: dict[str, Any]) -> str | None:
    """Hard pre-flight check: a Gemini service is selected but no key is set."""
    uses_gemini = "gemini" in (
        config.get("transcription_service", "gemini"),
        config.get("summarization_service", "gemini"),
    )
    if uses_gemini and not config.get("gemini_api_key"):
        return "Gemini API key is not configured. Please open Settings."
    return None


def gemini_key_warning(config: dict[str, Any]) -> str | None:
    """Return a human-readable warning if the configured Gemini key looks wrong.

    Pure (unit-testable). Only a *format* check — no network call. Google API
    keys start with "AIza"; a mismatch almost always means a paste error, and
    catching it at save time beats a failed job at the end of a meeting.
    """
    uses_gemini = "gemini" in (
        config.get("transcription_service", "gemini"),
        config.get("summarization_service", "gemini"),
    )
    if not uses_gemini:
        return None
    key = (config.get("gemini_api_key") or "").strip()
    if not key:
        return "Gemini is selected as a service but no API key is set."
    if not key.startswith("AIza") or len(key) < 35:
        return (
            "The Gemini API key does not look like a Google API key "
            '(expected to start with "AIza"). Double-check it in Settings.'
        )
    return None
