package com.github.meetingrecorder.audio

import android.content.Context
import android.media.MediaRecorder
import java.io.File

class AudioRecorder(private val context: Context) {

    private var recorder: MediaRecorder? = null

    fun start(outputDir: File, bitrate: Int = 64_000): File {
        stop()
        val file = File(outputDir, "recording.m4a")
        recorder = MediaRecorder(context).apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setAudioEncodingBitRate(bitrate)
            setAudioSamplingRate(44_100)
            setOutputFile(file.absolutePath)
            prepare()
            start()
        }
        return file
    }

    fun stop() {
        recorder?.apply {
            try {
                stop()
            } catch (_: RuntimeException) {
                // stop() throws if called in an invalid state (e.g. no audio data recorded)
            }
            release()
        }
        recorder = null
    }

    val isRecording: Boolean get() = recorder != null
}
