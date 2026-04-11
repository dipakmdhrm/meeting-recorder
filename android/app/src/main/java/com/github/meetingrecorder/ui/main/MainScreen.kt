package com.github.meetingrecorder.ui.main

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.DisposableEffect
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    onNavigateToSettings: () -> Unit,
    onNavigateToMeetings: () -> Unit,
    viewModel: MainViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsState()
    val context = LocalContext.current

    var showTitleDialog by remember { mutableStateOf(false) }
    var pendingTitle by remember { mutableStateOf("") }
    var showNoApiKeyDialog by remember { mutableStateOf(false) }
    var hasStoragePermission by remember { mutableStateOf(Environment.isExternalStorageManager()) }

    // Re-check storage permission every time the screen resumes (user returning from Settings)
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                hasStoragePermission = Environment.isExternalStorageManager()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val audioPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) showTitleDialog = true
    }

    val storageSettingsLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { /* re-check happens on next tap */ }

    fun requestPermissionsAndRecord() {
        if (!viewModel.hasApiKey()) {
            showNoApiKeyDialog = true
            return
        }
        if (!hasStoragePermission) {
            val intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION).apply {
                data = Uri.parse("package:${context.packageName}")
            }
            storageSettingsLauncher.launch(intent)
            return
        }
        audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
    }

    if (showNoApiKeyDialog) {
        AlertDialog(
            onDismissRequest = { showNoApiKeyDialog = false },
            title = { Text("API key required") },
            text = { Text("Add your Gemini API key in Settings before recording.") },
            confirmButton = {
                TextButton(onClick = {
                    showNoApiKeyDialog = false
                    onNavigateToSettings()
                }) { Text("Open Settings") }
            },
            dismissButton = {
                TextButton(onClick = { showNoApiKeyDialog = false }) { Text("Cancel") }
            },
        )
    }

    if (showTitleDialog) {
        AlertDialog(
            onDismissRequest = { showTitleDialog = false },
            title = { Text("Meeting title (optional)") },
            text = {
                OutlinedTextField(
                    value = pendingTitle,
                    onValueChange = { pendingTitle = it },
                    label = { Text("Title") },
                    placeholder = { Text("e.g. Standup, Design Review…") },
                    singleLine = true,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    showTitleDialog = false
                    viewModel.startRecording(pendingTitle.ifBlank { null })
                    pendingTitle = ""
                }) { Text("Start recording") }
            },
            dismissButton = {
                TextButton(onClick = { showTitleDialog = false }) { Text("Cancel") }
            },
        )
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Meeting Recorder") },
                actions = {
                    IconButton(onClick = onNavigateToMeetings) {
                        Icon(Icons.AutoMirrored.Filled.List, contentDescription = "Meetings")
                    }
                    IconButton(onClick = onNavigateToSettings) {
                        Icon(Icons.Default.Settings, contentDescription = "Settings")
                    }
                },
            )
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentAlignment = Alignment.Center,
        ) {
            when (val s = state) {
                is RecordingState.Ready -> ReadyContent(
                    onRecord = { requestPermissionsAndRecord() },
                    storagePermissionMissing = !hasStoragePermission,
                    onGrantStorage = {
                        val intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION).apply {
                            data = Uri.parse("package:${context.packageName}")
                        }
                        storageSettingsLauncher.launch(intent)
                    },
                )

                is RecordingState.Recording -> RecordingContent(
                    elapsedSecs = s.elapsedSecs,
                    onStop = { viewModel.stopRecording() },
                )

                is RecordingState.Processing -> ProcessingContent(statusMsg = s.statusMsg)

                is RecordingState.Done -> DoneContent(
                    notes = s.notes,
                    onSave = { viewModel.saveResults() },
                    onDiscard = { viewModel.discardResults() },
                )

                is RecordingState.Error -> ErrorContent(
                    msg = s.msg,
                    onDismiss = { viewModel.dismissError() },
                )
            }
        }
    }
}

@Composable
private fun ReadyContent(
    onRecord: () -> Unit,
    storagePermissionMissing: Boolean,
    onGrantStorage: () -> Unit,
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp),
        modifier = Modifier.padding(16.dp),
    ) {
        if (storagePermissionMissing) {
            OutlinedCard(modifier = Modifier.fillMaxWidth()) {
                Column(
                    modifier = Modifier.padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        "Storage permission required",
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                    Text(
                        "Grant 'All files access' so recordings can be saved to Documents/Meetings.",
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Button(onClick = onGrantStorage) { Text("Grant permission") }
                }
            }
        }
        Text("Ready to record", style = MaterialTheme.typography.bodyLarge)
        FloatingActionButton(onClick = onRecord) {
            Icon(Icons.Default.Mic, contentDescription = "Start recording")
        }
    }
}

@Composable
private fun RecordingContent(elapsedSecs: Int, onStop: () -> Unit) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        val mins = elapsedSecs / 60
        val secs = elapsedSecs % 60
        Text(
            text = "Recording… %02d:%02d".format(mins, secs),
            style = MaterialTheme.typography.headlineSmall,
        )
        FloatingActionButton(
            onClick = onStop,
            containerColor = MaterialTheme.colorScheme.error,
        ) {
            Icon(Icons.Default.Stop, contentDescription = "Stop recording")
        }
    }
}

@Composable
private fun ProcessingContent(statusMsg: String) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        CircularProgressIndicator()
        Text(statusMsg, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun DoneContent(notes: String, onSave: () -> Unit, onDiscard: () -> Unit) {
    Column(
        modifier = Modifier.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Processing complete!", style = MaterialTheme.typography.titleMedium)
        OutlinedCard {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Notes preview:", style = MaterialTheme.typography.labelMedium)
                Text(
                    text = if (notes.length > 400) notes.take(400) + "…" else notes,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onSave) { Text("Save") }
            OutlinedButton(onClick = onDiscard) { Text("Discard") }
        }
    }
}

@Composable
private fun ErrorContent(msg: String, onDismiss: () -> Unit) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
        modifier = Modifier.padding(16.dp),
    ) {
        Text("Error: $msg", color = MaterialTheme.colorScheme.error)
        Button(onClick = onDismiss) { Text("OK") }
    }
}
