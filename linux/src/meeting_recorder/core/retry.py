"""
Retry helper for transient network failures.

One failed HTTP roundtrip used to fail an entire transcription job even when
the upload itself would have succeeded a second later. Providers wrap their
network calls in :func:`retry_on_transient` so brief hiccups (timeouts,
connection resets, 5xx, 429) are absorbed, while permanent errors (bad API
key, invalid request, model errors) still fail immediately.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP statuses worth retrying: server-side failures and rate limiting.
_TRANSIENT_HTTP_STATUSES = set(range(500, 600)) | {429}


def is_transient(exc: BaseException) -> bool:
    """Best-effort classification of *exc* as a transient (retryable) failure.

    Works across the client stacks in use (urllib, httpx via google-genai)
    without importing them: connection/timeout errors by type, HTTP errors by
    a ``code``/``status_code``/``status`` attribute in the 5xx/429 range.
    """
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    # urllib.error.HTTPError has .code; httpx.HTTPStatusError carries a
    # response with .status_code; some SDK errors expose .status_code/.status.
    for attr in ("code", "status_code", "status"):
        value: Any = getattr(exc, attr, None)
        if isinstance(value, int) and value in _TRANSIENT_HTTP_STATUSES:
            return True
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and status in _TRANSIENT_HTTP_STATUSES:
        return True
    # Timeout-ish errors from httpx/httpcore don't subclass TimeoutError.
    name = type(exc).__name__.lower()
    return "timeout" in name


def retry_on_transient(
    fn: Callable[[], T],
    *,
    retries: int = 2,
    backoff_seconds: float = 2.0,
    is_transient_fn: Callable[[BaseException], bool] = is_transient,
    sleep_fn: Callable[[float], None] = time.sleep,
    description: str = "operation",
) -> T:
    """Call ``fn()``, retrying up to *retries* times on transient failures.

    Backoff doubles per attempt (2 s, 4 s, ...). Non-transient exceptions
    propagate immediately; the last transient exception propagates once the
    attempts are exhausted.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if attempt >= retries or not is_transient_fn(exc):
                raise
            delay = backoff_seconds * (2**attempt)
            attempt += 1
            logger.warning(
                "Transient failure in %s (attempt %d/%d): %s — retrying in %.0fs",
                description,
                attempt,
                retries,
                exc,
                delay,
            )
            sleep_fn(delay)
