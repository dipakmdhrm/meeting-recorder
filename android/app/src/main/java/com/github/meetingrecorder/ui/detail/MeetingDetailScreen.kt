package com.github.meetingrecorder.ui.detail

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.github.meetingrecorder.R


@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MeetingDetailScreen(
    meetingPath: String,
    onBack: () -> Unit,
    viewModel: MeetingDetailViewModel = viewModel(),
) {
    LaunchedEffect(meetingPath) { viewModel.load(meetingPath) }

    val transcript by viewModel.transcript.collectAsState()
    val notes by viewModel.notes.collectAsState()
    val hasAudio by viewModel.hasAudio.collectAsState()
    val genState by viewModel.genState.collectAsState()
    val currentNotes = notes
    val currentTranscript = transcript
    val processing = genState is GenState.Processing
    var selectedTab by remember { mutableIntStateOf(0) }
    var menuExpanded by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.title_meeting_detail)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.action_back))
                    }
                },
                actions = {
                    // Regenerate is offered for notes only, and only once notes exist.
                    if (selectedTab == 0 && currentNotes != null && !processing) {
                        IconButton(onClick = { menuExpanded = true }) {
                            Icon(Icons.Default.MoreVert, contentDescription = stringResource(R.string.cd_more_options))
                        }
                        DropdownMenu(expanded = menuExpanded, onDismissRequest = { menuExpanded = false }) {
                            DropdownMenuItem(
                                text = { Text(stringResource(R.string.action_regenerate_notes)) },
                                onClick = {
                                    menuExpanded = false
                                    viewModel.regenerateNotes()
                                },
                            )
                        }
                    }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            TabRow(selectedTabIndex = selectedTab) {
                Tab(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    text = { Text(stringResource(R.string.label_notes)) },
                )
                Tab(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    text = { Text(stringResource(R.string.label_transcript)) },
                )
                if (hasAudio) {
                    Tab(
                        selected = selectedTab == 2,
                        onClick = { selectedTab = 2 },
                        text = { Text(stringResource(R.string.label_audio)) },
                    )
                }
            }

            when (selectedTab) {
                0 -> NotesTabBody(
                    notes = currentNotes,
                    transcript = currentTranscript,
                    hasAudio = hasAudio,
                    genState = genState,
                    onGenerateNotes = viewModel::generateNotes,
                    onGenerateAll = viewModel::generateTranscriptAndNotes,
                )
                1 -> TranscriptTabBody(
                    transcript = currentTranscript,
                    hasAudio = hasAudio,
                    genState = genState,
                    onGenerateAll = viewModel::generateTranscriptAndNotes,
                )
                2 -> AudioPlayer(viewModel)
            }
        }
    }
}

@Composable
private fun NotesTabBody(
    notes: String?,
    transcript: String?,
    hasAudio: Boolean,
    genState: GenState,
    onGenerateNotes: () -> Unit,
    onGenerateAll: () -> Unit,
) {
    when {
        genState is GenState.Processing -> ProcessingBody(genState.status)
        notes != null -> Column(Modifier.fillMaxSize()) {
            // Surface regenerate failures without losing the existing notes.
            if (genState is GenState.Error) ErrorBanner(genState.msg)
            ContentText(notes, Modifier.weight(1f))
        }
        // Reached only when notes are absent, so hasNotes is false here.
        else -> EmptyState(genState = genState, placeholderRes = R.string.no_notes) {
            when (notesTabAction(hasNotes = false, hasTranscript = transcript != null, hasAudio = hasAudio)) {
                // Transcript already present → just summarize it, no audio re-upload.
                GenerateAction.NOTES_ONLY -> Button(onClick = onGenerateNotes) {
                    Text(stringResource(R.string.action_generate_notes))
                }
                GenerateAction.TRANSCRIBE_AND_NOTES -> Button(onClick = onGenerateAll) {
                    Text(stringResource(R.string.action_generate_transcript_notes))
                }
                GenerateAction.NONE -> {}
            }
        }
    }
}

@Composable
private fun TranscriptTabBody(
    transcript: String?,
    hasAudio: Boolean,
    genState: GenState,
    onGenerateAll: () -> Unit,
) {
    when {
        // Keep the transcript readable even while notes are being generated in the background.
        transcript != null -> ContentText(transcript)
        genState is GenState.Processing -> ProcessingBody(genState.status)
        // Reached only when the transcript is absent, so hasTranscript is false here.
        else -> EmptyState(genState = genState, placeholderRes = R.string.no_transcript) {
            if (transcriptTabAction(hasTranscript = false, hasAudio = hasAudio) ==
                GenerateAction.TRANSCRIBE_AND_NOTES
            ) {
                Button(onClick = onGenerateAll) {
                    Text(stringResource(R.string.action_generate_transcript_notes))
                }
            }
        }
    }
}

@Composable
private fun ContentText(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        modifier = modifier
            .fillMaxSize()
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        style = MaterialTheme.typography.bodySmall,
    )
}

@Composable
private fun ErrorBanner(msg: String) {
    Text(
        text = msg,
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
        color = MaterialTheme.colorScheme.error,
        style = MaterialTheme.typography.bodyMedium,
    )
}

@Composable
private fun ProcessingBody(status: String) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        CircularProgressIndicator()
        Text(
            text = status,
            modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@Composable
private fun EmptyState(
    genState: GenState,
    placeholderRes: Int,
    action: @Composable () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        if (genState is GenState.Error) {
            Text(
                text = genState.msg,
                modifier = Modifier.fillMaxWidth().padding(bottom = 16.dp),
                textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
        } else {
            Text(
                text = stringResource(placeholderRes),
                modifier = Modifier.padding(bottom = 16.dp),
            )
        }
        action()
    }
}
