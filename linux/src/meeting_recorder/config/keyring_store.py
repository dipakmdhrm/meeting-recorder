"""
Secret Service (keyring) storage for the Gemini API key.

When a D-Bus Secret Service is available (GNOME Keyring, KWallet with the
secrets portal, KeePassXC), the API key lives there instead of in plaintext
config.json. Everything degrades gracefully: if ``secretstorage`` is not
importable or the service is unreachable, ``available()`` is False and the
chmod-600 config.json remains the storage, exactly as before.

The secretstorage module is injectable so the store is unit-testable without
a D-Bus session.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ATTRIBUTES = {"application": "meeting-recorder", "purpose": "gemini-api-key"}
_LABEL = "Meeting Recorder — Gemini API key"

_UNSET = object()


class KeyringStore:
    """Stores one secret (the Gemini API key) in the session keyring."""

    def __init__(self, secretstorage_module: Any = _UNSET) -> None:
        if secretstorage_module is _UNSET:
            try:
                import secretstorage

                secretstorage_module = secretstorage
            except ImportError:
                secretstorage_module = None
        self._ss = secretstorage_module

    # ------------------------------------------------------------------

    def available(self) -> bool:
        """True if the Secret Service can be reached right now.

        Deliberately does NOT unlock the collection: this is called from
        startup/save paths, and triggering a synchronous password prompt from
        a mere availability probe would be intrusive. get()/set() unlock when
        the secret is actually needed.
        """
        if self._ss is None:
            return False
        try:
            conn = self._ss.dbus_init()
            try:
                self._ss.get_default_collection(conn)
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Secret Service unavailable: %s", exc)
            return False

    def get(self) -> str | None:
        """Return the stored key, or None if absent/unavailable."""
        if self._ss is None:
            return None
        try:
            conn, collection = self._open()
            try:
                for item in collection.search_items(_ATTRIBUTES):
                    secret = item.get_secret()
                    return bytes(secret).decode("utf-8")
                return None
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Could not read API key from keyring: %s", exc)
            return None

    def set(self, value: str) -> bool:
        """Store *value*, replacing any previous entry. Returns success."""
        if self._ss is None:
            return False
        try:
            conn, collection = self._open()
            try:
                collection.create_item(_LABEL, _ATTRIBUTES, value.encode("utf-8"), replace=True)
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Could not store API key in keyring: %s", exc)
            return False

    def delete(self) -> bool:
        """Remove the stored key (e.g. when the user clears it). Returns success."""
        if self._ss is None:
            return False
        try:
            conn, collection = self._open()
            try:
                for item in collection.search_items(_ATTRIBUTES):
                    item.delete()
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Could not delete API key from keyring: %s", exc)
            return False

    # ------------------------------------------------------------------

    def _open(self) -> tuple[Any, Any]:
        conn = self._ss.dbus_init()
        try:
            collection = self._ss.get_default_collection(conn)
            if collection.is_locked():
                collection.unlock()
        except Exception:
            # Close the connection on failure (e.g. the user dismissed the
            # unlock prompt) so repeated failures don't leak descriptors.
            conn.close()
            raise
        return conn, collection
