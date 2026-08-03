"""
Tests for the pure tray helpers.

The tray is a D-Bus StatusNotifierItem; the D-Bus wiring is integration-level and
stays outside unit scope (see the test-coverage boundaries in CLAUDE.md). The
icon-selection and menu-construction *policy* is pure and is guarded here.

The helpers live in ``tray_model`` (gi-free) so this test runs without PyGObject
— the CI test environment installs only pytest.
"""

from meeting_recorder.ui.tray_model import assign_menu_ids, build_menu_model, icon_for_state


def _labels(items):
    return [i.get("label") for i in items if i["type"] != "separator"]


def _actions(items):
    return [i["action"] for i in items if i["type"] == "action"]


class TestIconForState:
    def test_recording_wins(self):
        # Recording takes priority even when jobs are active.
        assert icon_for_state("recording", [("j", lambda: None)]) == "meeting-recorder-recording"

    def test_paused(self):
        assert icon_for_state("paused", []) == "meeting-recorder-paused"

    def test_jobs_when_idle(self):
        assert icon_for_state("idle", [("j", lambda: None)]) == "meeting-recorder-processing"

    def test_idle_no_jobs(self):
        assert icon_for_state("idle", []) == "meeting-recorder"


class TestBuildMenuModel:
    def test_idle_controls(self):
        items = build_menu_model("idle", [])
        assert _actions(items)[:3] == ["record_headphones", "record_speaker", "use_existing"]
        # Footer is always present.
        assert "show" in _actions(items)
        assert "quit" in _actions(items)

    def test_recording_controls(self):
        items = build_menu_model("recording", [])
        assert _actions(items)[:4] == ["pause", "stop", "cancel_save", "cancel"]

    def test_paused_controls(self):
        items = build_menu_model("paused", [])
        assert _actions(items)[:4] == ["resume", "stop", "cancel_save", "cancel"]

    def test_no_jobs_section_when_empty(self):
        items = build_menu_model("idle", [])
        assert not any("Processing" in (i.get("label") or "") for i in items)
        assert not any(i.get("action") == "cancel_job" for i in items)

    def test_jobs_section(self):
        jobs = [("Standup", lambda: None), ("Sync", lambda: None)]
        items = build_menu_model("recording", jobs)

        header = [i for i in items if i["type"] == "label"]
        assert header and header[0]["label"] == "Processing (2 active)"
        assert header[0]["enabled"] is False

        cancel_items = [i for i in items if i.get("action") == "cancel_job"]
        assert [i["label"] for i in cancel_items] == ["  Cancel: Standup", "  Cancel: Sync"]
        assert [i["job_index"] for i in cancel_items] == [0, 1]

    def test_separators_between_sections(self):
        jobs = [("Standup", lambda: None)]
        items = build_menu_model("recording", jobs)
        # One separator before the jobs section, one before the footer.
        assert sum(1 for i in items if i["type"] == "separator") == 2

    def test_idle_has_only_footer_separator(self):
        items = build_menu_model("idle", [])
        assert sum(1 for i in items if i["type"] == "separator") == 1


class TestAssignMenuIds:
    def test_stamps_sequential_ids(self):
        model = build_menu_model("idle", [])
        nxt = assign_menu_ids(model, 1)
        assert [i["id"] for i in model] == list(range(1, len(model) + 1))
        assert nxt == len(model) + 1

    def test_ids_never_reused_across_rebuilds(self):
        # A recording→idle transition must not hand any idle item an id that
        # a recording item used, or dbusmenu hosts merge stale props (the ghost
        # "Cancel"/disabled "Open" bug). Fresh ids guarantee disjoint id sets.
        recording = build_menu_model("recording", [])
        nxt = assign_menu_ids(recording, 1)
        idle = build_menu_model("idle", [])
        assign_menu_ids(idle, nxt)

        recording_ids = {i["id"] for i in recording}
        idle_ids = {i["id"] for i in idle}
        assert recording_ids.isdisjoint(idle_ids)

    def test_counter_advances_monotonically(self):
        nxt = 1
        seen: set[int] = set()
        for state in ("idle", "recording", "paused", "idle"):
            model = build_menu_model(state, [])
            new_nxt = assign_menu_ids(model, nxt)
            ids = {i["id"] for i in model}
            assert ids.isdisjoint(seen)  # no id ever repeats
            assert new_nxt > nxt
            seen |= ids
            nxt = new_nxt
