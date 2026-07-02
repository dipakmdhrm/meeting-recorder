package com.github.meetingrecorder

import android.content.Context
import android.os.Environment
import android.os.storage.StorageManager
import com.github.meetingrecorder.data.Config
import com.github.meetingrecorder.data.GeminiClient
import com.github.meetingrecorder.data.MeetingRepository
import java.io.File

/**
 * Manual dependency container — the app is small enough that a hand-wired container is clearer
 * than a DI framework. Owned by [MeetingRecorderApp]; ViewModels receive these dependencies via
 * constructor injection through [appViewModelFactory] instead of casting `application`.
 */
class AppContainer(context: Context) {

    val config: Config = Config(context)

    val meetingRepository: MeetingRepository =
        MeetingRepository(File(documentsDirectory(context), "Meetings"))

    /** Shared Gemini client; it reads the latest key/model/prompts from [config] on every call. */
    val geminiClient: GeminiClient by lazy { GeminiClient(config) }

    /**
     * Resolves the shared `Documents` directory on primary external storage
     * (`/storage/emulated/0/Documents`) without `Environment.getExternalStoragePublicDirectory()`
     * (docs-deprecated since API 29). `StorageVolume.getDirectory()` (API 30+; minSdk is 31)
     * reports the same root that call resolved against, so the on-disk location is unchanged; the
     * fallback covers the (rare) case where the primary volume reports no mounted directory.
     */
    private fun documentsDirectory(context: Context): File {
        val primaryRoot = context.getSystemService(StorageManager::class.java)
            ?.primaryStorageVolume
            ?.directory
            ?: Environment.getExternalStorageDirectory()
        return File(primaryRoot, Environment.DIRECTORY_DOCUMENTS)
    }
}
