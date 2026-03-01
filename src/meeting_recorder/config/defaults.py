"""Default values, model lists, and constants."""

from __future__ import annotations

APP_ID = "com.github.mint-meeting-recorder"
APP_NAME = "Meeting Recorder"
CONFIG_DIR = "~/.config/meeting-recorder"
CONFIG_FILE = "~/.config/meeting-recorder/config.json"
DEFAULT_OUTPUT_FOLDER = "~/meetings"

TRANSCRIPTION_SERVICES = ["gemini", "whisper"]
SUMMARIZATION_SERVICES = ["gemini", "gpt4o"]

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-pro-preview-03-25",
]

OPENAI_TRANSCRIPTION_MODELS = [
    "whisper-1",
]

OPENAI_SUMMARIZATION_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
]



DEFAULT_CONFIG: dict = {
    "transcription_service": "gemini",
    "summarization_service": "gemini",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    "openai_api_key": "",
    "openai_transcription_model": "whisper-1",
    "openai_summarization_model": "gpt-4o",

    "output_folder": DEFAULT_OUTPUT_FOLDER,
    "call_detection_enabled": False,
}

# Whisper file size limit in bytes (25MB)
WHISPER_SIZE_LIMIT = 25 * 1024 * 1024
# Chunk target size (20MB)
WHISPER_CHUNK_SIZE = 20 * 1024 * 1024
# Overlap in seconds for chunked transcription
WHISPER_OVERLAP_SECONDS = 30

# Deduplication window (seconds) — don't notify again within this period
CALL_DETECTION_DEDUP_WINDOW = 10

# Recording format
AUDIO_FORMAT = "mp3"
AUDIO_CODEC = "libmp3lame"
AUDIO_QUALITY = "2"  # VBR quality (0=best, 9=worst); q:a 2 ≈ 190kbps

# Named pipe buffer for ffmpeg
FFMPEG_THREAD_QUEUE_SIZE = 512

SUMMARIZATION_PROMPT = """\
You are a meeting assistant. Given the following meeting transcript, produce concise, \
well-structured meeting notes in Markdown format.

The transcript may include speaker labels (e.g. **Speaker 1:**, **John:**). \
Where speaker labels are present, reference speakers by name or label when attributing \
decisions and key points.

Structure the notes as follows:
1. A brief summary of the meeting (2-4 sentences).
2. Key discussion points and decisions, attributed to speakers where identifiable.
3. If and only if there are clear action items mentioned in the meeting, add an \
## Action Items section at the very end. List each item as a checkbox with the owner \
if known (e.g. `- [ ] John to send the report by Friday`). \
If there are no action items, omit this section entirely — do not write "None".

TRANSCRIPT:
{transcript}
"""

GEMINI_TRANSCRIPTION_PROMPT = """\
Transcribe this audio recording exactly as spoken.

Audio channel layout:
- Left channel = local microphone (the person who made this recording)
- Right channel = system audio (remote participants)

Use this channel information to distinguish speakers. Label each speaker turn with a \
timestamp and speaker label on a new line, for example:

[00:00:05] **You:** Hello, can everyone hear me?
[00:00:09] **Speaker 2:** Yes, loud and clear.

Rules:
- Use **You:** for the left-channel (local mic) speaker.
- Use the person's name if you can infer it from the conversation; otherwise use \
**Speaker 2:**, **Speaker 3:**, etc.
- Start each new speaker turn on a new line.
- Timestamps should be in [HH:MM:SS] format, incremented roughly every turn.
- Transcribe faithfully in whatever language is spoken; do not translate.
"""

GEMINI_DUAL_PROMPT = """\
You are a meeting assistant. Transcribe this audio recording, then produce meeting notes.

Audio channel layout:
- Left channel = local microphone (the person who made this recording)
- Right channel = system audio (remote participants)

Use this channel information to distinguish speakers. Label each speaker turn with a \
timestamp and speaker label, for example:

[00:00:05] **You:** Hello, can everyone hear me?
[00:00:09] **Speaker 2:** Yes, loud and clear.

Rules:
- Use **You:** for the left-channel (local mic) speaker.
- Use the person's name if you can infer it from the conversation; otherwise use \
**Speaker 2:**, **Speaker 3:**, etc.
- Timestamps in [HH:MM:SS] format.
- Transcribe faithfully in whatever language is spoken; do not translate.

Format your response EXACTLY as follows (keep the delimiter lines exactly as shown):

--- TRANSCRIPT ---
[Timestamped, speaker-labelled transcript, one turn per line]

--- NOTES ---
[Concise meeting notes in Markdown format:
1. Brief summary (2-4 sentences).
2. Key discussion points and decisions, attributed to speakers where identifiable.
3. If and only if there are clear action items, end with a ## Action Items section \
listing each as a checkbox with owner if known (e.g. `- [ ] John to send the report`). \
Omit this section entirely if there are no action items.]
"""
