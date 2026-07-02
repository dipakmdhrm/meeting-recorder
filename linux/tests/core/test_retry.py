"""Tests for retry_on_transient and the is_transient classifier."""

import urllib.error

import pytest

from meeting_recorder.core.retry import is_transient, retry_on_transient


class _FlakyThenOk:
    """Callable that fails *failures* times, then returns a value."""

    def __init__(self, failures: int, exc: Exception):
        self.failures = failures
        self.exc = exc
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return "ok"


class TestIsTransient:
    def test_timeout_error(self):
        assert is_transient(TimeoutError()) is True

    def test_connection_error(self):
        assert is_transient(ConnectionResetError()) is True

    def test_http_5xx_via_code_attribute(self):
        err = urllib.error.HTTPError("http://x", 503, "unavailable", None, None)
        assert is_transient(err) is True

    def test_http_429_via_code_attribute(self):
        err = urllib.error.HTTPError("http://x", 429, "rate limited", None, None)
        assert is_transient(err) is True

    def test_http_4xx_not_transient(self):
        err = urllib.error.HTTPError("http://x", 401, "unauthorized", None, None)
        assert is_transient(err) is False

    def test_status_code_attribute(self):
        class SdkError(Exception):
            status_code = 502

        assert is_transient(SdkError()) is True

    def test_response_status_code(self):
        class Response:
            status_code = 500

        class HttpxLikeError(Exception):
            response = Response()

        assert is_transient(HttpxLikeError()) is True

    def test_timeout_by_type_name(self):
        class ReadTimeout(Exception):
            pass

        assert is_transient(ReadTimeout()) is True

    def test_value_error_not_transient(self):
        assert is_transient(ValueError("bad input")) is False


class TestRetryOnTransient:
    def test_returns_result_without_retry_on_success(self):
        fn = _FlakyThenOk(0, TimeoutError())
        assert retry_on_transient(fn, sleep_fn=lambda _: None) == "ok"
        assert fn.calls == 1

    def test_retries_transient_then_succeeds(self):
        fn = _FlakyThenOk(2, TimeoutError())
        assert retry_on_transient(fn, retries=2, sleep_fn=lambda _: None) == "ok"
        assert fn.calls == 3

    def test_raises_after_exhausting_retries(self):
        fn = _FlakyThenOk(5, TimeoutError())
        with pytest.raises(TimeoutError):
            retry_on_transient(fn, retries=2, sleep_fn=lambda _: None)
        assert fn.calls == 3  # initial + 2 retries

    def test_permanent_error_fails_immediately(self):
        fn = _FlakyThenOk(1, ValueError("bad api key"))
        with pytest.raises(ValueError):
            retry_on_transient(fn, retries=5, sleep_fn=lambda _: None)
        assert fn.calls == 1

    def test_backoff_doubles(self):
        delays: list[float] = []
        fn = _FlakyThenOk(2, TimeoutError())
        retry_on_transient(fn, retries=2, backoff_seconds=2.0, sleep_fn=delays.append)
        assert delays == [2.0, 4.0]
