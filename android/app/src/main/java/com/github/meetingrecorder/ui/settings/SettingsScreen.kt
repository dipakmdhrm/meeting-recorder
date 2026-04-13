package com.github.meetingrecorder.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.github.meetingrecorder.R
import com.github.meetingrecorder.data.AudioQuality
import com.github.meetingrecorder.data.Config

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    viewModel: SettingsViewModel = viewModel(),
) {
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.title_settings)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.action_back))
                    }
                },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            TabRow(selectedTabIndex = selectedTab) {
                Tab(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    text = { Text(stringResource(R.string.tab_general)) },
                )
                Tab(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    text = { Text(stringResource(R.string.tab_prompts)) },
                )
            }

            when (selectedTab) {
                0 -> GeneralTab(viewModel)
                1 -> PromptsTab(viewModel)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun GeneralTab(viewModel: SettingsViewModel) {
    val apiKey by viewModel.apiKey.collectAsState()
    val model by viewModel.model.collectAsState()
    val audioQuality by viewModel.audioQuality.collectAsState()
    val processingCountdownEnabled by viewModel.processingCountdownEnabled.collectAsState()

    var apiKeyDraft by rememberSaveable { mutableStateOf(apiKey) }
    var modelDraft by rememberSaveable { mutableStateOf(model) }
    var audioQualityDraft by rememberSaveable { mutableStateOf(audioQuality) }
    var processingCountdownDraft by rememberSaveable { mutableStateOf(processingCountdownEnabled) }
    var modelMenuExpanded by remember { mutableStateOf(false) }
    var qualityMenuExpanded by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        OutlinedTextField(
            value = apiKeyDraft,
            onValueChange = { apiKeyDraft = it },
            label = { Text(stringResource(R.string.label_gemini_api_key)) },
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
        )

        ExposedDropdownMenuBox(
            expanded = modelMenuExpanded,
            onExpandedChange = { modelMenuExpanded = it },
        ) {
            OutlinedTextField(
                value = modelDraft,
                onValueChange = {},
                readOnly = true,
                label = { Text(stringResource(R.string.label_model)) },
                trailingIcon = {
                    ExposedDropdownMenuDefaults.TrailingIcon(expanded = modelMenuExpanded)
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .menuAnchor(MenuAnchorType.PrimaryNotEditable),
            )
            ExposedDropdownMenu(
                expanded = modelMenuExpanded,
                onDismissRequest = { modelMenuExpanded = false },
            ) {
                viewModel.availableModels.forEach { m ->
                    DropdownMenuItem(
                        text = { Text(m) },
                        onClick = {
                            modelDraft = m
                            modelMenuExpanded = false
                        },
                    )
                }
            }
        }

        ExposedDropdownMenuBox(
            expanded = qualityMenuExpanded,
            onExpandedChange = { qualityMenuExpanded = it },
        ) {
            OutlinedTextField(
                value = audioQualityDraft.label,
                onValueChange = {},
                readOnly = true,
                label = { Text(stringResource(R.string.label_recording_quality)) },
                trailingIcon = {
                    ExposedDropdownMenuDefaults.TrailingIcon(expanded = qualityMenuExpanded)
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .menuAnchor(MenuAnchorType.PrimaryNotEditable),
            )
            ExposedDropdownMenu(
                expanded = qualityMenuExpanded,
                onDismissRequest = { qualityMenuExpanded = false },
            ) {
                viewModel.availableQualities.forEach { q ->
                    DropdownMenuItem(
                        text = { Text(q.label) },
                        onClick = {
                            audioQualityDraft = q
                            qualityMenuExpanded = false
                        },
                    )
                }
            }
        }

        HorizontalDivider()

        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(stringResource(R.string.label_processing_countdown), style = MaterialTheme.typography.bodyMedium)
                Text(
                    stringResource(R.string.desc_processing_countdown),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Switch(
                checked = processingCountdownDraft,
                onCheckedChange = { processingCountdownDraft = it },
            )
        }

        Button(
            onClick = {
                viewModel.setApiKey(apiKeyDraft)
                viewModel.setModel(modelDraft)
                viewModel.setAudioQuality(audioQualityDraft)
                viewModel.setProcessingCountdownEnabled(processingCountdownDraft)
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(stringResource(R.string.action_save))
        }
    }
}

@Composable
private fun PromptsTab(viewModel: SettingsViewModel) {
    val transcriptionPrompt by viewModel.transcriptionPrompt.collectAsState()
    val summarizationPrompt by viewModel.summarizationPrompt.collectAsState()
    val titlePrompt by viewModel.titlePrompt.collectAsState()

    var transcriptionDraft by rememberSaveable {
        mutableStateOf(transcriptionPrompt.ifBlank { Config.DEFAULT_TRANSCRIPTION_PROMPT })
    }
    var summarizationDraft by rememberSaveable {
        mutableStateOf(summarizationPrompt.ifBlank { Config.DEFAULT_SUMMARIZATION_PROMPT })
    }
    var titleDraft by rememberSaveable {
        mutableStateOf(titlePrompt.ifBlank { Config.DEFAULT_TITLE_PROMPT })
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        PromptField(
            label = stringResource(R.string.label_transcription_prompt),
            value = transcriptionDraft,
            onValueChange = { transcriptionDraft = it },
            isDefault = transcriptionDraft == Config.DEFAULT_TRANSCRIPTION_PROMPT,
        )
        PromptField(
            label = stringResource(R.string.label_summarization_prompt),
            value = summarizationDraft,
            onValueChange = { summarizationDraft = it },
            isDefault = summarizationDraft == Config.DEFAULT_SUMMARIZATION_PROMPT,
        )
        PromptField(
            label = stringResource(R.string.label_meeting_title_prompt),
            value = titleDraft,
            onValueChange = { titleDraft = it },
            isDefault = titleDraft == Config.DEFAULT_TITLE_PROMPT,
        )
        Button(
            onClick = {
                // Store blank when the user hasn't changed from the default,
                // so the app continues to pick up future default changes.
                viewModel.setTranscriptionPrompt(
                    if (transcriptionDraft == Config.DEFAULT_TRANSCRIPTION_PROMPT) "" else transcriptionDraft
                )
                viewModel.setSummarizationPrompt(
                    if (summarizationDraft == Config.DEFAULT_SUMMARIZATION_PROMPT) "" else summarizationDraft
                )
                viewModel.setTitlePrompt(
                    if (titleDraft == Config.DEFAULT_TITLE_PROMPT) "" else titleDraft
                )
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(stringResource(R.string.action_save))
        }
    }
}

@Composable
private fun PromptField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    isDefault: Boolean = false,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        supportingText = if (isDefault) {
            { Text(stringResource(R.string.hint_showing_default)) }
        } else null,
        modifier = Modifier.fillMaxWidth(),
        minLines = 5,
    )
}
