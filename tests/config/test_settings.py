import os
from unittest.mock import patch

from meeting_recorder.config.settings import inject_api_keys


def test_inject_api_keys_sets_env_vars():
    config = {"api_keys": {"TEST_KEY_ABC": "secret123"}}
    with patch.dict(os.environ, {}, clear=False):
        inject_api_keys(config)
        assert os.environ.get("TEST_KEY_ABC") == "secret123"


def test_inject_api_keys_skips_empty_values():
    config = {"api_keys": {"EMPTY_KEY": "", "VALID_KEY": "val"}}
    with patch.dict(os.environ, {}, clear=False):
        inject_api_keys(config)
        assert "EMPTY_KEY" not in os.environ
        assert os.environ.get("VALID_KEY") == "val"


def test_inject_api_keys_handles_missing_key():
    config = {}
    # Should not raise
    inject_api_keys(config)
