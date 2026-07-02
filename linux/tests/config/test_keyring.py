"""
Tests for KeyringStore and the settings keyring integration.

The secretstorage module is replaced with an in-memory fake; config paths are
redirected to a temp dir — no D-Bus and no real config touched.
"""

import json

import pytest

from meeting_recorder.config import settings
from meeting_recorder.config.keyring_store import KeyringStore

# ── in-memory fake of the secretstorage API surface we use ───────────────────


class FakeItem:
    def __init__(self, collection, attrs, secret):
        self._collection = collection
        self.attrs = attrs
        self.secret = secret

    def get_secret(self):
        return self.secret

    def delete(self):
        self._collection.items.remove(self)


class FakeCollection:
    def __init__(self, locked=False):
        self.items: list[FakeItem] = []
        self._locked = locked

    def is_locked(self):
        return self._locked

    def unlock(self):
        self._locked = False

    def search_items(self, attrs):
        return [i for i in self.items if i.attrs == attrs]

    def create_item(self, label, attrs, secret, replace=False):
        if replace:
            self.items = [i for i in self.items if i.attrs != attrs]
        item = FakeItem(self, attrs, secret)
        self.items.append(item)
        return item


class FakeConnection:
    def close(self):
        pass


class FakeSecretStorage:
    def __init__(self, collection=None, fail=False):
        self.collection = collection or FakeCollection()
        self.fail = fail

    def dbus_init(self):
        if self.fail:
            raise OSError("no D-Bus session")
        return FakeConnection()

    def get_default_collection(self, conn):
        return self.collection


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def config_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "_config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(settings, "_config_dir", lambda: tmp_path)
    return tmp_path


def _store(**kw) -> KeyringStore:
    return KeyringStore(secretstorage_module=FakeSecretStorage(**kw))


# ── KeyringStore ──────────────────────────────────────────────────────────────


class TestKeyringStore:
    def test_set_get_roundtrip(self):
        store = _store()
        assert store.set("AIzaSecret123") is True
        assert store.get() == "AIzaSecret123"

    def test_set_replaces_previous_value(self):
        store = _store()
        store.set("old")
        store.set("new")
        assert store.get() == "new"

    def test_get_returns_none_when_empty(self):
        assert _store().get() is None

    def test_delete_removes_entry(self):
        store = _store()
        store.set("secret")
        assert store.delete() is True
        assert store.get() is None

    def test_unavailable_without_module(self):
        store = KeyringStore(secretstorage_module=None)
        assert store.available() is False
        assert store.get() is None
        assert store.set("x") is False

    def test_unavailable_when_dbus_fails(self):
        store = _store(fail=True)
        assert store.available() is False
        assert store.get() is None

    def test_unlocks_locked_collection(self):
        store = KeyringStore(
            secretstorage_module=FakeSecretStorage(collection=FakeCollection(locked=True))
        )
        assert store.set("secret") is True
        assert store.get() == "secret"


# ── settings integration ─────────────────────────────────────────────────────


class TestSettingsKeyringIntegration:
    def test_save_puts_key_in_keyring_and_sentinel_on_disk(self, config_in_tmp):
        store = _store()
        settings.save({"gemini_api_key": "AIzaReal"}, keyring=store)
        on_disk = json.loads((config_in_tmp / "config.json").read_text())
        assert on_disk["gemini_api_key"] == settings.KEYRING_SENTINEL
        assert store.get() == "AIzaReal"

    def test_load_resolves_sentinel_from_keyring(self, config_in_tmp):
        store = _store()
        settings.save({"gemini_api_key": "AIzaReal"}, keyring=store)
        cfg = settings.load(keyring=store)
        assert cfg["gemini_api_key"] == "AIzaReal"

    def test_save_falls_back_to_plaintext_without_keyring(self, config_in_tmp):
        store = KeyringStore(secretstorage_module=None)
        settings.save({"gemini_api_key": "AIzaReal"}, keyring=store)
        on_disk = json.loads((config_in_tmp / "config.json").read_text())
        assert on_disk["gemini_api_key"] == "AIzaReal"
        assert settings.load(keyring=store)["gemini_api_key"] == "AIzaReal"

    def test_load_returns_empty_when_keyring_lost(self, config_in_tmp):
        settings.save({"gemini_api_key": "AIzaReal"}, keyring=_store())
        # A different (empty) keyring simulates the secret being gone.
        cfg = settings.load(keyring=_store())
        assert cfg["gemini_api_key"] == ""

    def test_clearing_key_deletes_keyring_entry(self, config_in_tmp):
        store = _store()
        settings.save({"gemini_api_key": "AIzaReal"}, keyring=store)
        settings.save({"gemini_api_key": ""}, keyring=store)
        assert store.get() is None

    def test_migration_moves_plaintext_key(self, config_in_tmp):
        # Simulate an existing pre-keyring config with a plaintext key.
        (config_in_tmp / "config.json").write_text(json.dumps({"gemini_api_key": "AIzaLegacy"}))
        store = _store()
        assert settings.migrate_key_to_keyring(keyring=store) is True
        on_disk = json.loads((config_in_tmp / "config.json").read_text())
        assert on_disk["gemini_api_key"] == settings.KEYRING_SENTINEL
        assert store.get() == "AIzaLegacy"

    def test_migration_noop_without_keyring(self, config_in_tmp):
        (config_in_tmp / "config.json").write_text(json.dumps({"gemini_api_key": "AIzaLegacy"}))
        store = KeyringStore(secretstorage_module=None)
        assert settings.migrate_key_to_keyring(keyring=store) is False
        on_disk = json.loads((config_in_tmp / "config.json").read_text())
        assert on_disk["gemini_api_key"] == "AIzaLegacy"  # untouched

    def test_migration_noop_when_already_migrated(self, config_in_tmp):
        store = _store()
        settings.save({"gemini_api_key": "AIzaReal"}, keyring=store)
        assert settings.migrate_key_to_keyring(keyring=store) is False

    def test_migration_noop_without_config_file(self, config_in_tmp):
        assert settings.migrate_key_to_keyring(keyring=_store()) is False
