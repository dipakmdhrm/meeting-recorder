#!/usr/bin/env python3
"""
Test the FULL Gemini pipeline (transcription + summarization) on one audio file.

Reuses the app's real ``Pipeline``, so output matches what the applet would produce — only
the models are swappable per run. Models default to your config.json when the flags are
omitted. Outputs land in <repo>/tmp/test-<timestamp>/.

    scripts/test-full-gemini.py --audio /path/audio.mp3 \\
        --transcription-model gemini-pro-latest --summarization-model gemini-flash-latest
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from _gemini_test_common import (
    load_gemini_config,
    make_run_dir,
    prompt_source,
    status_printer,
    write_run_info,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path, help="Path to the audio file.")
    parser.add_argument(
        "--transcription-model",
        default=None,
        help="Gemini model for transcription (default: config's gemini_transcription_model).",
    )
    parser.add_argument(
        "--summarization-model",
        default=None,
        help="Gemini model for summarization (default: config's gemini_summarization_model).",
    )
    args = parser.parse_args()

    if not args.audio.exists():
        parser.error(f"audio file not found: {args.audio}")

    config = load_gemini_config(
        {
            "gemini_transcription_model": args.transcription_model,
            "gemini_summarization_model": args.summarization_model,
        }
    )

    from meeting_recorder.processing.pipeline import Pipeline

    run_dir = make_run_dir()
    transcript_path = run_dir / "transcript.md"
    notes_path = run_dir / "notes.md"

    print(f"Transcription model: {config['gemini_transcription_model']}")
    print(f"Summarization model: {config['gemini_summarization_model']}")
    print(f"Output folder:       {run_dir}")

    started = time.monotonic()
    pipeline = Pipeline(
        config,
        audio_path=args.audio.resolve(),
        transcript_path=transcript_path,
        notes_path=notes_path,
        on_status=status_printer("full"),
    )
    pipeline.run()
    elapsed = time.monotonic() - started

    write_run_info(
        run_dir,
        {
            "mode": "full (transcription + summarization)",
            "audio": args.audio.resolve(),
            "transcription_model": config["gemini_transcription_model"],
            "summarization_model": config["gemini_summarization_model"],
            "transcription_prompt": prompt_source(config, "transcription_prompt"),
            "summarization_prompt": prompt_source(config, "summarization_prompt"),
            "elapsed_seconds": f"{elapsed:.1f}",
        },
    )

    print(f"\nDone in {elapsed:.1f}s.")
    print(f"  transcript: {transcript_path}")
    print(f"  notes:      {notes_path}")
    print(f"  run info:   {run_dir / 'run-info.txt'}")


if __name__ == "__main__":
    main()
