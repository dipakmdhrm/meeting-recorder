"""
Manages persistent application configuration. It provides functions to load and save user settings from a JSON file, ensuring that sensitive data like API keys are stored with restricted file permissions and merged with default values.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

from .defaults import CONFIG_DIR, CONFIG_FILE, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


def _config_path() -> Path:
    return Path(os.path.expanduser(CONFIG_FILE))


def _config_dir() -> Path:
    return Path(os.path.expanduser(CONFIG_DIR))


def load() -> dict[str, Any]:
    """Load config, returning defaults merged with stored values."""
    path = _config_path()
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            with open(path) as f:
                stored = json.load(f)
            # Merge: stored values override defaults, unknown keys ignored
            for key in DEFAULT_CONFIG:
                if key in stored:
                    config[key] = stored[key]
        except Exception as exc:
            logger.warning("Failed to load config: %s", exc)
    return config


def save(config: dict[str, Any]) -> None:
    """Save config to disk with 600 permissions."""
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
        # file is world-readable. The config stores API keys in plaintext.
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


def inject_api_keys(config: dict[str, Any] | None = None) -> None:
    """Inject api_keys dict entries into os.environ."""
    if config is None:
        config = load()
    for env_name, value in config.get("api_keys", {}).items():
        if value:
            os.environ[env_name] = value
