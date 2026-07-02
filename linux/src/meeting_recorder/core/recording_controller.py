"""
Headless orchestration of the recording lifecycle, extracted from MainWindow.

The controller owns the Recorder instance, the stop/processing countdown, and
the authoritative lifecycle State; the window is reduced to rendering state
changes and forwarding button clicks. All GTK dependencies are injected
(countdown scheduler, recorder factory, device validation), so the whole
lifecycle — including the countdown and cancel flows — is unit-testable
without a display.

Threading contract: all public methods are main-thread only. `on_timer` and
`on_recorder_error` are forwarded straight from the Recorder's worker threads
— the window wraps them with idle_call. Everything else fires on the main
thread (TaskRunner routes worker results back).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config.defaults import RECORDING_QUALITIES
from ..utils.filename import output_paths
from .state_machine import State, can_transition
from .task_runner import TaskRunner

logger = logging.getLogger(__name__)

COUNTDOWN_SECONDS = 5
STOP_WAIT_TIMEOUT = 35.0


@dataclass
class PendingRecording:
    """Paths and label of a recording that is stopping / about to be processed."""

    audio_path: Path
    transcript_path: Path
    notes_path: Path
    label: str


def make_job_label(audio_path: Path, title: str | None) -> str:
    """Human label for a job row: the meeting dir's time part plus the title."""
    time_part = audio_path.parent.name if audio_path else "recording"
    return f"{time_part} {title}".strip() if title else time_part


def _glib_schedule_tick(interval_ms: int, callback: Callable[[], bool]) -> None:
    from gi.repository import GLib

    GLib.timeout_add(interval_ms, callback)


def _default_validate_devices() -> tuple[bool, str]:
    from ..audio.devices import validate_devices

    return validate_devices()


def _default_recorder_factory(**kwargs: Any) -> Any:
    from ..audio.recorder import Recorder

    return Recorder(**kwargs)


class RecordingController:
    """Owns start/pause/resume/stop/cancel and the processing countdown."""

    def __init__(
        self,
        runner: TaskRunner,
        *,
        on_state: Callable[[State, str], None],
        on_error: Callable[[str], None],
        on_commit: Callable[[PendingRecording], None],
        on_saved: Callable[[PendingRecording], None],
        on_discarded: Callable[[], None],
        on_countdown: Callable[[int], None],
        on_timer: Callable[[int], None],
        on_recorder_error: Callable[[str], None],
        recorder_factory: Callable[..., Any] | None = None,
        validate_devices_fn: Callable[[], tuple[bool, str]] | None = None,
        schedule_tick: Callable[[int, Callable[[], bool]], None] | None = None,
    ) -> None:
        self._runner = runner
        self._on_state = on_state
        self._on_error = on_error
        self._on_commit = on_commit
        self._on_saved = on_saved
        self._on_discarded = on_discarded
        self._on_countdown = on_countdown
        self._on_timer = on_timer
        self._on_recorder_error = on_recorder_error
        self._recorder_factory = recorder_factory or _default_recorder_factory
        self._validate_devices = validate_devices_fn or _default_validate_devices
        self._schedule_tick = schedule_tick or _glib_schedule_tick

        self._state = State.IDLE
        self._recorder: Any | None = None
        self._pending: PendingRecording | None = None
        self._countdown_gen = 0
        self._countdown_remaining = 0
        self._recorder_done = threading.Event()

    # ------------------------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    def start(self, cfg: dict[str, Any], mode: str, title: str | None) -> None:
        """Validate and start a recording. Emits on_error instead of raising."""
        if self._state != State.IDLE:
            return

        ok, err = self._validate_devices()
        if not ok:
            self._on_error(f"Audio device error: {err}")
            return

        audio, transcript, notes = output_paths(cfg.get("output_folder", "~/meetings"), title)
        self._pending = PendingRecording(
            audio_path=audio,
            transcript_path=transcript,
            notes_path=notes,
            label=make_job_label(audio, title),
        )

        q_key = cfg.get("recording_quality", "high")
        _, q_val = RECORDING_QUALITIES.get(q_key, RECORDING_QUALITIES["high"])

        recorder = self._recorder_factory(
            output_path=audio,
            mode=mode,
            quality=q_val,
            on_tick=self._on_timer,
            on_error=self._on_recorder_error,
        )
        try:
            recorder.start()
        except Exception as exc:
            self._pending = None
            self._on_error(str(exc))
            return
        self._recorder = recorder

        mode_label = "headphones" if mode == "headphones" else "speaker"
        self._set_state(State.RECORDING, f"Recording… ({mode_label} mode)")

    def pause(self) -> None:
        if self._state != State.RECORDING or not self._recorder:
            return
        self._recorder.pause()
        self._set_state(State.PAUSED, "Paused")

    def resume(self) -> None:
        if self._state != State.PAUSED or not self._recorder:
            return
        self._recorder.resume()
        self._set_state(State.RECORDING, "Recording…")

    def stop(self, countdown_enabled: bool) -> None:
        """Stop recording; commit the pending job now or after a countdown."""
        if self._state not in (State.RECORDING, State.PAUSED) or not self._recorder:
            return

        self._countdown_gen += 1
        gen = self._countdown_gen
        recorder, self._recorder = self._recorder, None

        self._recorder_done.clear()
        self._runner.submit(self._stop_recorder_worker, recorder, description="stop recorder")

        if countdown_enabled:
            self._countdown_remaining = COUNTDOWN_SECONDS
            self._set_state(State.COUNTDOWN, f"Starting transcription in {COUNTDOWN_SECONDS}s…")
            self._schedule_tick(1000, lambda: self._countdown_tick(gen))
        else:
            self._commit()

    def cancel_countdown(self) -> None:
        if self._state != State.COUNTDOWN:
            return
        self._countdown_gen += 1
        self._pending = None
        self._set_state(State.IDLE, "Transcription cancelled.")
        logger.info("Transcription cancelled during countdown.")

    def cancel_and_save(self) -> None:
        """Stop recording, keep the audio, skip transcription."""
        if self._state not in (State.RECORDING, State.PAUSED) or not self._recorder:
            return
        recorder, self._recorder = self._recorder, None
        pending, self._pending = self._pending, None
        self._set_state(State.IDLE, "Stopping recording…")

        def _done(_result: Any) -> None:
            self._on_state(State.IDLE, "Recording saved (no transcription).")
            if pending is not None:
                self._on_saved(pending)

        self._runner.submit(
            recorder.stop,
            on_done=_done,
            on_error=lambda exc: self._on_error(f"Failed to stop recording: {exc}"),
            description="stop recorder (cancel + save)",
        )

    def cancel_and_discard(self) -> None:
        """Stop recording and delete the audio."""
        if self._state not in (State.RECORDING, State.PAUSED) or not self._recorder:
            return
        recorder, self._recorder = self._recorder, None
        pending, self._pending = self._pending, None
        audio_path = pending.audio_path if pending else None
        self._set_state(State.IDLE, "Cancelling…")

        def _stop_and_discard() -> None:
            recorder.stop()
            if audio_path and audio_path.exists():
                try:
                    audio_path.unlink()
                except Exception as exc:
                    logger.warning("Could not delete audio file: %s", exc)
            if audio_path:
                try:
                    audio_path.parent.rmdir()
                except OSError:
                    pass  # directory not empty (other files) — leave it

        def _done(_result: Any) -> None:
            self._on_state(State.IDLE, "Recording discarded.")
            self._on_discarded()

        self._runner.submit(
            _stop_and_discard,
            on_done=_done,
            on_error=lambda exc: self._on_error(f"Failed to stop recording: {exc}"),
            description="stop recorder (discard)",
        )

    def abort_to_idle(self) -> None:
        """Recorder died mid-recording (device loss etc.) — reset the lifecycle."""
        self._recorder = None
        self._pending = None
        self._countdown_gen += 1
        self._set_state(State.IDLE, "")

    def wait_until_stopped(self, timeout: float = STOP_WAIT_TIMEOUT) -> None:
        """Block (worker threads only) until recorder.stop() has finished."""
        self._recorder_done.wait(timeout=timeout)

    # ------------------------------------------------------------------

    def _stop_recorder_worker(self, recorder: Any) -> None:
        try:
            recorder.stop()
        except Exception as exc:
            logger.error("Error stopping recorder: %s", exc)
        finally:
            self._recorder_done.set()

    def _countdown_tick(self, gen: int) -> bool:
        if gen != self._countdown_gen:
            return False  # cancelled or superseded
        self._countdown_remaining -= 1
        if self._countdown_remaining > 0:
            self._on_countdown(self._countdown_remaining)
            return True
        self._commit()
        return False

    def _commit(self) -> None:
        pending, self._pending = self._pending, None
        self._set_state(State.IDLE, "")
        if pending is not None:
            self._on_commit(pending)

    def _set_state(self, new_state: State, status: str) -> None:
        if not can_transition(self._state, new_state):
            logger.error("Illegal state transition %s -> %s", self._state.name, new_state.name)
        self._state = new_state
        self._on_state(new_state, status)
