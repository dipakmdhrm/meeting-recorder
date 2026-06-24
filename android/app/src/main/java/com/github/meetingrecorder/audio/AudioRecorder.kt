package com.github.meetingrecorder.audio

import android.content.Context
import android.media.AudioManager
import android.media.AudioRecordingConfiguration
import android.media.MediaRecorder
import java.io.File

class AudioRecorder(private val context: Context) {

    private var recorder: MediaRecorder? = null
    private val audioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    /**
     * True if the OS silenced our microphone input (e.g. an incoming call or a privacy mute)
     * or MediaRecorder reported an error while recording. When this is set, the resulting
     * file is likely silent even though it is non-empty on disk.
     */
    @Volatile
    var wasInterrupted: Boolean = false
        private set

    private var onInterrupted: (() -> Unit)? = null

    // Fires whenever the set of active recordings changes; isClientSilenced tells us the system
    // has muted our capture (the exact case that produces a non-empty but silent recording).
    private val recordingCallback = object : AudioManager.AudioRecordingCallback() {
        override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>?) {
            if (configs?.any { it.isClientSilenced } == true) markInterrupted()
        }
    }

    fun start(outputDir: File, bitrate: Int = 64_000, onInterrupted: (() -> Unit)? = null): File {
        stop()
        wasInterrupted = false
        this.onInterrupted = onInterrupted
        val file = File(outputDir, "recording.m4a")
        recorder = MediaRecorder(context).apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setAudioEncodingBitRate(bitrate)
            setAudioSamplingRate(44_100)
            setOutputFile(file.absolutePath)
            // MediaRecorder is otherwise silent about mid-recording failures (e.g. the mic being seized).
            setOnErrorListener { _, _, _ -> markInterrupted() }
            prepare()
            start()
        }
        audioManager.registerAudioRecordingCallback(recordingCallback, null)
        return file
    }

    fun stop() {
        recorder?.apply {
            audioManager.unregisterAudioRecordingCallback(recordingCallback)
            try {
                stop()
            } catch (_: RuntimeException) {
                // stop() throws if called in an invalid state (e.g. no audio data recorded) —
                // that itself means the recording captured nothing usable.
                markInterrupted()
            }
            release()
        }
        recorder = null
        onInterrupted = null
    }

    private fun markInterrupted() {
        if (!wasInterrupted) {
            wasInterrupted = true
            onInterrupted?.invoke()
        }
    }

    val isRecording: Boolean get() = recorder != null
}
