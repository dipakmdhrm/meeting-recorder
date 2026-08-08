#!/usr/bin/env python3
"""
Test Gemini TRANSCRIPTION only on one audio file.

Reuses the app's ``create_transcription_provider`` so the result matches the applet's
transcription stage. The model defaults to your config.json when --model is omitted.
Output lands in <repo>/tmp/test-<timestamp>/transcript.md.

    scripts/test-transcription-gemini.py --audio /path/audio.mp3 --model gemini-pro-latest
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
        "--model",
        default=None,
        help="Gemini transcription model (default: config's gemini_transcription_model).",
    )
    args = parser.parse_args()

    if not args.audio.exists():
        parser.error(f"audio file not found: {args.audio}")

    config = load_gemini_config({"gemini_transcription_model": args.model})

    from meeting_recorder.processing.transcription import create_transcription_provider

    run_dir = make_run_dir()
    transcript_path = run_dir / "transcript.md"

    print(f"Transcription model: {config['gemini_transcription_model']}")
    print(f"Output folder:       {run_dir}")

    started = time.monotonic()
    provider = create_transcription_provider(config)
    transcript = provider.transcribe(
        audio_path=args.audio.resolve(),
        on_status=status_printer("transcribe"),
    )
    elapsed = time.monotonic() - started

    transcript_path.write_text(transcript, encoding="utf-8")
    write_run_info(
        run_dir,
        {
            "mode": "transcription only",
            "audio": args.audio.resolve(),
            "transcription_model": config["gemini_transcription_model"],
            "transcription_prompt": prompt_source(config, "transcription_prompt"),
            "elapsed_seconds": f"{elapsed:.1f}",
        },
    )

    print(f"\nDone in {elapsed:.1f}s.")
    print(f"  transcript: {transcript_path}")
    print(f"  run info:   {run_dir / 'run-info.txt'}")


if __name__ == "__main__":
    main()
