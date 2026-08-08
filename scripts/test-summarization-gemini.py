#!/usr/bin/env python3
"""
Test Gemini SUMMARIZATION only, from an existing transcript file.

Reuses the app's ``create_summarization_provider`` so notes match the applet's
summarization stage — no re-upload/re-transcription. The model defaults to your config.json
when --model is omitted. Output lands in <repo>/tmp/test-<timestamp>/notes.md.

    scripts/test-summarization-gemini.py --transcript /path/transcript.md --model gemini-flash-latest
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
    parser.add_argument(
        "--transcript", required=True, type=Path, help="Path to the transcript .md file."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Gemini summarization model (default: config's gemini_summarization_model).",
    )
    args = parser.parse_args()

    if not args.transcript.exists():
        parser.error(f"transcript file not found: {args.transcript}")

    transcript_text = args.transcript.read_text(encoding="utf-8")
    if not transcript_text.strip():
        parser.error(f"transcript file is empty: {args.transcript}")

    config = load_gemini_config({"gemini_summarization_model": args.model})

    from meeting_recorder.processing.summarization import create_summarization_provider

    run_dir = make_run_dir()
    notes_path = run_dir / "notes.md"

    print(f"Summarization model: {config['gemini_summarization_model']}")
    print(f"Output folder:       {run_dir}")

    started = time.monotonic()
    provider = create_summarization_provider(config)
    notes = provider.summarize(transcript_text, on_status=status_printer("summarize"))
    elapsed = time.monotonic() - started

    notes_path.write_text(notes, encoding="utf-8")
    write_run_info(
        run_dir,
        {
            "mode": "summarization only",
            "transcript": args.transcript.resolve(),
            "summarization_model": config["gemini_summarization_model"],
            "summarization_prompt": prompt_source(config, "summarization_prompt"),
            "elapsed_seconds": f"{elapsed:.1f}",
        },
    )

    print(f"\nDone in {elapsed:.1f}s.")
    print(f"  notes:    {notes_path}")
    print(f"  run info: {run_dir / 'run-info.txt'}")


if __name__ == "__main__":
    main()
