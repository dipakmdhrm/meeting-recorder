"""
Tests for AudioWatcher restart/backoff behavior and the pure event matcher.

Processes are replaced by fakes via the injected spawn_fn; sleep/monotonic are
injected so the tests run instantly.
"""

import threading

from meeting_recorder.detection.audio_watcher import AudioWatcher, is_call_start_event


class FakeProc:
    """Simulates a pactl subscribe process emitting fixed lines then exiting."""

    def __init__(self, lines: list[str], returncode: int = 1):
        self.stdout = iter(line + "\n" for line in lines)
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True


class TestIsCallStartEvent:
    def test_matches_new_source_output(self):
        assert is_call_start_event("Event 'new' on source-output #123") is True

    def test_ignores_new_client(self):
        assert is_call_start_event("Event 'new' on client #456") is False

    def test_ignores_remove_source_output(self):
        assert is_call_start_event("Event 'remove' on source-output #123") is False

    def test_ignores_unrelated_lines(self):
        assert is_call_start_event("Event 'change' on sink #0") is False


class TestAudioWatcherRestart:
    def _run_watcher(self, procs, detections, *, stop_after_spawns, clock=None):
        """Run the watcher synchronously (call _run directly) with fake procs."""
        spawned = []
        watcher = None

        def spawn():
            if len(spawned) >= stop_after_spawns:
                watcher._stop.set()  # simulate app shutdown to end the loop
                raise FileNotFoundError  # unreachable guard; loop exits first
            proc = procs[len(spawned)]
            spawned.append(proc)
            return proc

        slept: list[float] = []
        times = iter(clock or [0.0] * 100)

        watcher = AudioWatcher(
            on_detected=detections.append,
            spawn_fn=spawn,
            sleep_fn=slept.append,
            monotonic_fn=lambda: next(times),
        )
        watcher._run()
        return spawned, slept

    def test_restarts_after_process_death_and_keeps_detecting(self):
        detections: list[str] = []
        procs = [
            FakeProc(["Event 'new' on source-output #1"]),
            FakeProc(["Event 'new' on source-output #2"]),
        ]
        spawned, slept = self._run_watcher(procs, detections, stop_after_spawns=2)

        assert len(spawned) == 2  # died once, restarted once
        assert detections == ["audio-stream", "audio-stream"]
        assert len(slept) == 2  # one backoff sleep per death

    def test_backoff_doubles_on_repeated_deaths(self):
        detections: list[str] = []
        procs = [FakeProc([]), FakeProc([]), FakeProc([])]
        _spawned, slept = self._run_watcher(procs, detections, stop_after_spawns=3)

        assert slept == [1.0, 2.0, 4.0]

    def test_backoff_resets_after_healthy_run(self):
        detections: list[str] = []
        procs = [FakeProc([]), FakeProc([]), FakeProc([])]
        # Clock pairs (start, end) per run: first run unhealthy (0→1s),
        # second run healthy (10→100s), third unhealthy again.
        clock = [0.0, 1.0, 10.0, 100.0, 200.0, 201.0]
        _spawned, slept = self._run_watcher(procs, detections, stop_after_spawns=3, clock=clock)

        # 1.0 (first death), reset→1.0 (after healthy run), then 2.0
        assert slept == [1.0, 1.0, 2.0]

    def test_missing_pactl_disables_watcher_without_restart_loop(self):
        detections: list[str] = []

        def spawn():
            raise FileNotFoundError

        slept: list[float] = []
        watcher = AudioWatcher(
            on_detected=detections.append,
            spawn_fn=spawn,
            sleep_fn=slept.append,
        )
        watcher._run()  # must return promptly, not loop
        assert detections == []
        assert slept == []

    def test_stop_prevents_restart(self):
        detections: list[str] = []
        proc = FakeProc(["Event 'new' on source-output #1"])
        spawn_count = [0]
        watcher = None

        def spawn():
            spawn_count[0] += 1
            return proc

        watcher = AudioWatcher(
            on_detected=lambda s: (detections.append(s), watcher.stop()),
            spawn_fn=spawn,
            sleep_fn=lambda _: None,
        )
        watcher._run()
        assert spawn_count[0] == 1  # no restart after stop()
        assert proc.terminated is True


class TestAudioWatcherThreading:
    def test_start_and_stop_via_thread(self):
        release = threading.Event()

        class BlockingProc(FakeProc):
            def __init__(self):
                super().__init__([])

                def gen():
                    release.wait(5)
                    if False:
                        yield ""  # pragma: no cover

                self.stdout = gen()

            def terminate(self):
                super().terminate()
                release.set()

        proc = BlockingProc()
        watcher = AudioWatcher(on_detected=lambda _: None, spawn_fn=lambda: proc)
        watcher.start()
        watcher.stop()
        watcher._thread.join(timeout=5)
        assert not watcher._thread.is_alive()
