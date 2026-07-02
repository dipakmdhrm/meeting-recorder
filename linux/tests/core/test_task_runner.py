"""
Tests for TaskRunner and CancelToken.

The main-thread scheduler is injected as an immediate call, so callbacks run
synchronously on the worker thread — no GLib main loop required.
"""

import threading
import time

import pytest

from meeting_recorder.core.task_runner import CancelToken, TaskRunner


def immediate(callback):
    callback()


class TestSubmit:
    def test_result_routed_to_on_done(self):
        runner = TaskRunner(schedule_on_main=immediate)
        done = threading.Event()
        results = []

        runner.submit(lambda: 42, on_done=lambda v: (results.append(v), done.set()))

        assert done.wait(2)
        assert results == [42]

    def test_positional_args_forwarded(self):
        runner = TaskRunner(schedule_on_main=immediate)
        done = threading.Event()
        results = []

        runner.submit(lambda a, b: a + b, 20, 22, on_done=lambda v: (results.append(v), done.set()))

        assert done.wait(2)
        assert results == [42]

    def test_exception_routed_to_on_error(self):
        runner = TaskRunner(schedule_on_main=immediate)
        failed = threading.Event()
        errors = []

        def boom():
            raise ValueError("kaputt")

        runner.submit(boom, on_error=lambda e: (errors.append(e), failed.set()))

        assert failed.wait(2)
        assert isinstance(errors[0], ValueError)
        assert "kaputt" in str(errors[0])

    def test_exception_without_on_error_is_logged(self, caplog):
        runner = TaskRunner(schedule_on_main=immediate)

        def boom():
            raise ValueError("kaputt")

        with caplog.at_level("ERROR"):
            runner.submit(boom, description="doomed task")
            runner.shutdown(grace_seconds=2)

        assert any("doomed task" in r.message for r in caplog.records)
        assert any("kaputt" in r.message for r in caplog.records)

    def test_on_done_exception_is_logged_not_raised(self, caplog):
        runner = TaskRunner(schedule_on_main=immediate)
        done = threading.Event()

        def bad_callback(_value):
            done.set()
            raise RuntimeError("callback exploded")

        with caplog.at_level("ERROR"):
            runner.submit(lambda: 1, on_done=bad_callback, description="cb task")
            assert done.wait(2)
            runner.shutdown(grace_seconds=2)

        assert any("cb task" in r.message and "raised" in r.message for r in caplog.records)

    def test_callbacks_go_through_injected_scheduler(self):
        scheduled = []
        done = threading.Event()

        def recording_scheduler(cb):
            scheduled.append(cb)
            cb()

        runner = TaskRunner(schedule_on_main=recording_scheduler)
        runner.submit(lambda: "x", on_done=lambda _v: done.set())

        assert done.wait(2)
        assert len(scheduled) == 1


class TestShutdown:
    def test_completed_tasks_are_not_reported_abandoned(self):
        runner = TaskRunner(schedule_on_main=immediate)
        done = threading.Event()
        runner.submit(lambda: None, on_done=lambda _v: done.set())
        assert done.wait(2)

        assert runner.shutdown(grace_seconds=2) == []

    def test_hung_task_is_reported_abandoned(self):
        runner = TaskRunner(schedule_on_main=immediate)
        release = threading.Event()
        started = threading.Event()

        def hang():
            started.set()
            release.wait(30)

        runner.submit(hang, description="hung ffmpeg")
        assert started.wait(2)

        abandoned = runner.shutdown(grace_seconds=0.2)
        assert abandoned == ["hung ffmpeg"]
        release.set()  # let the daemon thread finish

    def test_submit_after_shutdown_raises(self):
        runner = TaskRunner(schedule_on_main=immediate)
        runner.shutdown(grace_seconds=0)

        with pytest.raises(RuntimeError, match="shut down"):
            runner.submit(lambda: None)

    def test_active_descriptions_reflects_running_tasks(self):
        runner = TaskRunner(schedule_on_main=immediate)
        release = threading.Event()
        started = threading.Event()

        def hold():
            started.set()
            release.wait(30)

        runner.submit(hold, description="long job")
        assert started.wait(2)
        assert runner.active_descriptions() == ["long job"]

        release.set()
        deadline = time.monotonic() + 2
        while runner.active_descriptions() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runner.active_descriptions() == []


class TestCancelToken:
    def test_starts_uncancelled(self):
        assert CancelToken().cancelled is False

    def test_cancel_sets_flag(self):
        token = CancelToken()
        token.cancel()
        assert token.cancelled is True

    def test_visible_across_threads(self):
        token = CancelToken()
        seen = threading.Event()

        def worker():
            while not token.cancelled:
                time.sleep(0.005)
            seen.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        token.cancel()
        assert seen.wait(2)
