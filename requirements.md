## My use case:
- I usually take meetings and calls on my PC.
- I want to record the audio of the calls
- I want to transcribe the call store the transcript in markdown format
- I want to summarize the call and store in markdown format

## What I want to implement:
I want to implement a linux app:
- When the app is opened, it simply shows a record button.
- When user presses the record button:
  - The app starts recordin input and output from all channels.
  - The record button is hidden and user is show two new buttons pause and play
  - If user presses pause button, pause the recording and instead show a 'continue' recording button instead of pause button.
- When the user presses the stop button:
  - Save the recording file
  - Transcribe the recording
  - Summarize the transcript
- The app should also provide a system tray option to start, pause, resume and stop the recording.
  - On stopping, the process of transcribing and summarizing should automatically start

## Technical specification
- App should use anthropic API to:
  - Transcribe the call (by sending the recording to Anthropic and getting the transcription back)
  - Summarize the transcription and create meeting notes (by sending the transcript to Anthropic and getting the summary back)
- App should provide UI to configure anthropic api
