"""
Defines application-wide constants and default configuration settings. This includes app identification, supported Gemini models, default prompts for transcription and summarization, and audio quality presets.
"""

from __future__ import annotations

APP_ID = "com.github.mint-meeting-recorder"
APP_NAME = "Meeting Recorder"
CONFIG_DIR = "~/.config/meeting-recorder"
CONFIG_FILE = "~/.config/meeting-recorder/config.json"
DEFAULT_OUTPUT_FOLDER = "~/meetings"

TRANSCRIPTION_SERVICES = ["gemini"]
SUMMARIZATION_SERVICES = ["gemini"]

GEMINI_MODELS = [
    "gemini-3.1-pro",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-pro-preview-03-25",
    "gemini-2.5-pro-exp-03-25",
]



DEFAULT_CONFIG: dict = {
    "transcription_service": "gemini",
    "summarization_service": "gemini",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",

    "output_folder": DEFAULT_OUTPUT_FOLDER,
    "recording_quality": "high",
    "call_detection_enabled": False,

    # Empty string means "use the built-in default prompt".
    # Storing the prompt text directly lets the user revert to the default
    # by clearing the field, without needing a separate "use default" flag.
    "transcription_prompt": "",
    "summarization_prompt": "",
}

# A single call start can trigger multiple source-output events (browser tabs, virtual
# devices). This window collapses the burst into one notification.
CALL_DETECTION_DEDUP_WINDOW = 10

# Recording format
AUDIO_FORMAT = "mp3"

# FFmpeg quality mapping (-q:a for libmp3lame)
# 2: ~190kbps, 5: ~130kbps, 7: ~100kbps, 9: ~64kbps
RECORDING_QUALITIES = {
    "very_high": ("Very High Quality (~190kbps)", "2"),
    "high": ("High Quality (~130kbps)", "5"),
    "medium": ("Medium Quality (~100kbps)", "7"),
    "low": ("Low Quality (~64kbps)", "9"),
}

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

Label each speaker turn with a timestamp and speaker label on a new line, for example:

[00:00:05] **Alice:** Hello, can everyone hear me?
[00:00:09] **Bob:** Yes, loud and clear.

Rules:
- Try to infer each speaker's name from the conversation (e.g. if someone is addressed \
by name or introduces themselves). Use that name as their label.
- If a name cannot be determined, label speakers as **Person 1:**, **Person 2:**, etc., \
assigned in the order they first speak. Use the same label consistently for the same speaker.
- Start each new speaker turn on a new line.
- Timestamps should be in [HH:MM:SS] format, incremented roughly every turn.
- Transcribe faithfully in whatever language is spoken; do not translate.
"""

