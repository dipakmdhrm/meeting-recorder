package com.github.meetingrecorder.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
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
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
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
                    text = { Text("General") },
                )
                Tab(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    text = { Text("Prompts") },
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

    var apiKeyDraft by rememberSaveable { mutableStateOf(apiKey) }
    var modelDraft by remember { mutableStateOf(model) }
    var audioQualityDraft by remember { mutableStateOf(audioQuality) }
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
            label = { Text("Gemini API Key") },
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
                label = { Text("Model") },
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
                label = { Text("Recording Quality") },
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

        Button(
            onClick = {
                viewModel.setApiKey(apiKeyDraft)
                viewModel.setModel(modelDraft)
                viewModel.setAudioQuality(audioQualityDraft)
            },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("Save")
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
            label = "Transcription Prompt",
            value = transcriptionDraft,
            onValueChange = { transcriptionDraft = it },
            isDefault = transcriptionDraft == Config.DEFAULT_TRANSCRIPTION_PROMPT,
        )
        PromptField(
            label = "Summarization Prompt",
            value = summarizationDraft,
            onValueChange = { summarizationDraft = it },
            isDefault = summarizationDraft == Config.DEFAULT_SUMMARIZATION_PROMPT,
        )
        PromptField(
            label = "Meeting Title Prompt",
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
            Text("Save")
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
            { Text("Showing default — edit to customize") }
        } else null,
        modifier = Modifier.fillMaxWidth(),
        minLines = 5,
    )
}
