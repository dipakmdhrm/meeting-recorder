#!/usr/bin/env python3
"""
Test Gemini TRANSCRIPTION only on one audio file.

Reuses the app's ``create_transcription_provider`` so the result matches the applet's
transcription stage. The model defaults to your config.json when --model is omitted.
Output lands in <repo>/tmp/test-<timestamp>/transcript.md.

    scripts/test-transcription-gemini.py --audio /path/audio.mp3 --model gemini-pro-latest

Pass --stt to exercise the dedicated speech-to-text model (gemini-3.5-transcribe) instead
of the prompt-based path, and -v to print the per-request token usage the provider logs,
which is how you compare real cost between the two:

    scripts/test-transcription-gemini.py --audio /path/audio.mp3 --stt -v
"""

from __future__ import annotations

import argparse
import logging
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
        help="Gemini transcription model (default: the matching model from config.json).",
    )
    parser.add_argument(
        "--stt",
        action="store_true",
        help=(
            "Use the dedicated speech-to-text service (gemini-3.5-transcribe): "
            "diarized, word-timestamped, and chunked for long audio."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log INFO lines, including per-request token usage and the chunk plan.",
    )
    args = parser.parse_args()

    if not args.audio.exists():
        parser.error(f"audio file not found: {args.audio}")

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    service = "gemini_transcribe" if args.stt else "gemini"
    model_key = "gemini_transcribe_model" if args.stt else "gemini_transcription_model"
    config = load_gemini_config({"transcription_service": service, model_key: args.model})

    from meeting_recorder.processing.transcription import create_transcription_provider

    run_dir = make_run_dir()
    transcript_path = run_dir / "transcript.md"

    print(f"Transcription service: {service}")
    print(f"Transcription model:   {config[model_key]}")
    print(f"Output folder:         {run_dir}")

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
            "transcription_service": service,
            "transcription_model": config[model_key],
            # The dedicated speech-to-text model takes no prompt at all.
            "transcription_prompt": (
                "n/a (model takes no prompt)"
                if args.stt
                else prompt_source(config, "transcription_prompt")
            ),
            "elapsed_seconds": f"{elapsed:.1f}",
        },
    )

    print(f"\nDone in {elapsed:.1f}s.")
    print(f"  transcript: {transcript_path}")
    print(f"  run info:   {run_dir / 'run-info.txt'}")


if __name__ == "__main__":
    main()
