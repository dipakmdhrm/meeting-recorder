"""Tests for the Job model and the job-row action policy."""

from pathlib import Path

from meeting_recorder.core.job import Job, JobStatus, actions_for_status


def _job(**overrides) -> Job:
    defaults = dict(
        job_id=1,
        audio_path=Path("/tmp/m/recording.mp3"),
        transcript_path=Path("/tmp/m/transcript.md"),
        notes_path=Path("/tmp/m/notes.md"),
        label="10-30 standup",
    )
    defaults.update(overrides)
    return Job(**defaults)


class TestJobDefaults:
    def test_new_job_is_processing(self):
        assert _job().status is JobStatus.PROCESSING

    def test_new_job_not_cancelled(self):
        job = _job()
        assert job.cancelled is False
        assert job.token.cancelled is False

    def test_each_job_gets_its_own_token(self):
        a, b = _job(), _job(job_id=2)
        a.token.cancel()
        assert b.token.cancelled is False


class TestActionsForStatus:
    def test_processing_offers_cancel_only(self):
        assert actions_for_status(JobStatus.PROCESSING) == ("cancel",)

    def test_done_offers_open_folder_and_dismiss(self):
        assert actions_for_status(JobStatus.DONE) == ("open_folder", "dismiss")

    def test_error_offers_retry_and_dismiss(self):
        assert actions_for_status(JobStatus.ERROR) == ("retry", "dismiss")

    def test_every_status_has_at_least_one_action(self):
        for status in JobStatus:
            assert actions_for_status(status)
