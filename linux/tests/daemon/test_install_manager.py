"""Tests for the daemon install manager (dedup, progress, finished, listing)."""

import json

import pytest

from meeting_recorder.core.install_spec import InstallSpec, spec_to_json
from meeting_recorder.daemon.install_manager import InstallManager


class FakeLauncher:
    def __init__(self):
        self.launched = []

    def launch(self, spec_json, *, on_status, on_finished):
        handle = {"spec_json": spec_json, "on_status": on_status, "on_finished": on_finished}
        self.launched.append(handle)
        return handle


def _mgr():
    events = {"progress": [], "finished": []}
    launcher = FakeLauncher()
    mgr = InstallManager(
        on_progress=lambda k, t: events["progress"].append((k, t)),
        on_finished=lambda k, ok, m: events["finished"].append((k, ok, m)),
        launcher=launcher,
    )
    return mgr, launcher, events


def test_start_launches_and_lists_running():
    mgr, launcher, _ = _mgr()
    key = mgr.start(spec_to_json(InstallSpec(kind="ollama")))
    assert key == "ollama"
    assert len(launcher.launched) == 1
    running = json.loads(mgr.running_json())
    assert running == [{"key": "ollama", "status": "Starting…"}]


def test_duplicate_start_is_deduped():
    mgr, launcher, _ = _mgr()
    mgr.start(spec_to_json(InstallSpec(kind="ollama")))
    mgr.start(spec_to_json(InstallSpec(kind="ollama")))
    assert len(launcher.launched) == 1  # second request ignored


def test_different_models_run_concurrently():
    mgr, launcher, _ = _mgr()
    mgr.start(spec_to_json(InstallSpec(kind="whisper_model", model="small")))
    mgr.start(spec_to_json(InstallSpec(kind="whisper_model", model="large")))
    assert len(launcher.launched) == 2
    keys = {r["key"] for r in json.loads(mgr.running_json())}
    assert keys == {"whisper_model:small", "whisper_model:large"}


def test_progress_updates_status_and_notifies():
    mgr, launcher, events = _mgr()
    mgr.start(spec_to_json(InstallSpec(kind="ollama_model", model="llama3")))
    launcher.launched[0]["on_status"]("pulling 42%")
    assert events["progress"] == [("ollama_model:llama3", "pulling 42%")]
    assert json.loads(mgr.running_json())[0]["status"] == "pulling 42%"


def test_finished_removes_and_notifies():
    mgr, launcher, events = _mgr()
    mgr.start(spec_to_json(InstallSpec(kind="ollama")))
    launcher.launched[0]["on_finished"](True, "")
    assert events["finished"] == [("ollama", True, "")]
    assert json.loads(mgr.running_json()) == []
    # After finishing, the same install can be started again.
    mgr.start(spec_to_json(InstallSpec(kind="ollama")))
    assert len(launcher.launched) == 2


def test_start_rejects_bad_spec():
    mgr, _, _ = _mgr()
    with pytest.raises(ValueError):
        mgr.start('{"kind": "nope"}')
