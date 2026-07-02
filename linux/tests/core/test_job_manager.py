"""Tests for JobManager persistence and startup recovery (temp dirs, no GTK)."""

import json
from pathlib import Path

from meeting_recorder.core.job import JobStatus
from meeting_recorder.core.job_manager import INTERRUPTED_MSG, JobManager, restore_status


def _mgr(tmp_path: Path) -> JobManager:
    return JobManager(state_dir=tmp_path)


def _create(mgr: JobManager, label: str = "job"):
    return mgr.create(
        audio_path=Path("/m/recording.mp3"),
        transcript_path=Path("/m/transcript.md"),
        notes_path=Path("/m/notes.md"),
        label=label,
    )


class TestRestoreStatusPolicy:
    def test_processing_becomes_interrupted_error(self):
        assert restore_status("processing") == (JobStatus.ERROR, INTERRUPTED_MSG)

    def test_error_restored_as_error_keeping_message(self):
        assert restore_status("error") == (JobStatus.ERROR, None)

    def test_done_is_dropped(self):
        assert restore_status("done") is None

    def test_unknown_status_is_dropped(self):
        assert restore_status("garbage") is None


class TestPersistence:
    def test_create_persists_to_disk(self, tmp_path):
        mgr = _mgr(tmp_path)
        _create(mgr, "standup")
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["label"] == "standup"
        assert data["jobs"][0]["status"] == "processing"

    def test_mark_done_and_error_persist(self, tmp_path):
        mgr = _mgr(tmp_path)
        a = _create(mgr, "a")
        b = _create(mgr, "b")
        mgr.mark_done(a)
        mgr.mark_error(b, "boom")
        data = json.loads((tmp_path / "jobs.json").read_text())
        statuses = {j["label"]: (j["status"], j["error_msg"]) for j in data["jobs"]}
        assert statuses == {"a": ("done", None), "b": ("error", "boom")}

    def test_remove_persists(self, tmp_path):
        mgr = _mgr(tmp_path)
        job = _create(mgr)
        mgr.remove(job)
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert data["jobs"] == []
        assert mgr.jobs == []

    def test_cancelled_jobs_are_not_persisted(self, tmp_path):
        mgr = _mgr(tmp_path)
        job = _create(mgr)
        job.cancelled = True
        mgr.persist()
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert data["jobs"] == []

    def test_no_partial_file_on_disk(self, tmp_path):
        mgr = _mgr(tmp_path)
        _create(mgr)
        # atomic write: only the final file remains, no tmp leftovers
        assert sorted(p.name for p in tmp_path.iterdir()) == ["jobs.json"]


class TestStartupRecovery:
    def test_interrupted_processing_job_reoffered_as_error(self, tmp_path):
        first = _mgr(tmp_path)
        _create(first, "meeting A")  # stays PROCESSING — simulates a crash

        second = _mgr(tmp_path)
        restored = second.load_persisted()
        assert len(restored) == 1
        assert restored[0].status is JobStatus.ERROR
        assert restored[0].error_msg == INTERRUPTED_MSG
        assert restored[0].label == "meeting A"

    def test_error_job_restored_with_original_message(self, tmp_path):
        first = _mgr(tmp_path)
        job = _create(first, "meeting B")
        first.mark_error(job, "quota exceeded")

        restored = _mgr(tmp_path).load_persisted()
        assert restored[0].error_msg == "quota exceeded"

    def test_done_jobs_not_reoffered_and_pruned_from_disk(self, tmp_path):
        first = _mgr(tmp_path)
        job = _create(first)
        first.mark_done(job)

        second = _mgr(tmp_path)
        assert second.load_persisted() == []
        data = json.loads((tmp_path / "jobs.json").read_text())
        assert data["jobs"] == []

    def test_ids_do_not_collide_after_restore(self, tmp_path):
        first = _mgr(tmp_path)
        old = _create(first)

        second = _mgr(tmp_path)
        second.load_persisted()
        new = _create(second)
        assert new.job_id != old.job_id

    def test_missing_file_starts_empty(self, tmp_path):
        assert _mgr(tmp_path).load_persisted() == []

    def test_corrupt_file_starts_empty(self, tmp_path):
        (tmp_path / "jobs.json").write_text("{not json")
        assert _mgr(tmp_path).load_persisted() == []

    def test_malformed_entry_skipped_others_restored(self, tmp_path):
        (tmp_path / "jobs.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "next_id": 5,
                    "jobs": [
                        {"status": "processing"},  # missing required fields
                        {
                            "job_id": 3,
                            "audio_path": "/m/r.mp3",
                            "transcript_path": "/m/t.md",
                            "notes_path": "/m/n.md",
                            "label": "good",
                            "status": "processing",
                            "error_msg": None,
                        },
                    ],
                }
            )
        )
        restored = _mgr(tmp_path).load_persisted()
        assert [j.label for j in restored] == ["good"]

    def test_restored_jobs_get_fresh_tokens(self, tmp_path):
        first = _mgr(tmp_path)
        _create(first)
        restored = _mgr(tmp_path).load_persisted()
        assert restored[0].token.cancelled is False


class TestMalformedStateFiles:
    def test_non_dict_json_starts_empty(self, tmp_path):
        (tmp_path / "jobs.json").write_text('["not", "an", "object"]')
        assert _mgr(tmp_path).load_persisted() == []

    def test_malformed_next_id_tolerated(self, tmp_path):
        (tmp_path / "jobs.json").write_text(
            json.dumps({"version": 1, "next_id": "garbage", "jobs": []})
        )
        mgr = _mgr(tmp_path)
        assert mgr.load_persisted() == []
        job = _create(mgr)  # id allocation must still work
        assert job.job_id == 0
