"""
Tests for ``resolve_existing_recording_target`` — the pure decision behind
"Use Existing Recording" (reuse a file already in the meetings tree in place vs.
treat an external file as a new import to copy).
"""

from meeting_recorder.utils.recording_import import resolve_existing_recording_target


class TestResolveExistingRecordingTarget:
    def test_file_inside_meeting_subdir_is_reused(self, tmp_path):
        output = tmp_path / "meetings"
        session = output / "2026" / "March" / "04" / "14-30_Standup"
        session.mkdir(parents=True)
        audio = session / "recording.m4a"
        audio.write_bytes(b"x")

        reuse, paths = resolve_existing_recording_target(audio, output)

        assert reuse is True
        assert paths == (audio, session / "transcript.md", session / "notes.md")

    def test_external_file_is_new_import(self, tmp_path):
        output = tmp_path / "meetings"
        output.mkdir()
        external = tmp_path / "Downloads" / "call.mp3"
        external.parent.mkdir(parents=True)
        external.write_bytes(b"x")

        reuse, paths = resolve_existing_recording_target(external, output)

        assert reuse is False
        assert paths is None

    def test_file_directly_in_output_root_is_not_reused(self, tmp_path):
        # A file sitting directly in the output folder (not in a session
        # subdirectory) is treated as external — there is no meeting dir to reuse.
        output = tmp_path / "meetings"
        output.mkdir()
        loose = output / "stray.wav"
        loose.write_bytes(b"x")

        reuse, paths = resolve_existing_recording_target(loose, output)

        assert reuse is False
        assert paths is None

    def test_sibling_folder_with_shared_prefix_is_not_reused(self, tmp_path):
        # "meetings-backup" shares a string prefix with "meetings" but is not
        # inside it — the os.sep guard must reject it.
        output = tmp_path / "meetings"
        output.mkdir()
        other = tmp_path / "meetings-backup" / "rec.m4a"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"x")

        reuse, paths = resolve_existing_recording_target(other, output)

        assert reuse is False
        assert paths is None
