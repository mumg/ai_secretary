package net.muratov.assistant

import android.app.Activity
import android.os.Bundle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.getValue
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import net.muratov.assistant.i18n.LanguageSetting
import net.muratov.assistant.i18n.LocalizedActivity
import net.muratov.assistant.i18n.tr
import net.muratov.assistant.setup.SetupWizard
import net.muratov.assistant.ui.ImproverTheme
import net.muratov.assistant.ui.RelationshipSettings
import net.muratov.assistant.updates.UpdatePanel
import net.muratov.assistant.updates.UpdatePrompt

class SettingsActivity : LocalizedActivity() {
    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as ImproverApplication).container
        setContent {
            ImproverTheme {
                var showSetup by rememberSaveable { mutableStateOf(false) }
                var connectionChanged by rememberSaveable { mutableStateOf(false) }
                SideEffect { if (connectionChanged) setResult(Activity.RESULT_OK) }
                UpdatePrompt(container.updates)
                if (showSetup) {
                    BackHandler { showSetup = false }
                    SetupWizard(
                        container.settings,
                        onComplete = {
                            container.realtime.connectionSettingsChanged()
                            connectionChanged = true
                            showSetup = false
                        },
                        onCancel = { showSetup = false },
                    )
                } else {
                    Scaffold(
                        topBar = {
                            TopAppBar(
                                title = { Text(tr("Настройки")) },
                                navigationIcon = {
                                    IconButton(onClick = ::finish) {
                                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = tr("Назад"))
                                    }
                                },
                            )
                        },
                    ) { padding ->
                        Column(
                            Modifier.fillMaxSize().padding(padding)
                                .verticalScroll(rememberScrollState()).padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(16.dp),
                        ) {
                            LanguageSetting()
                            Text(container.settings.serverUrl)
                            Button(onClick = { showSetup = true }) { Text(tr("Мастер подключения")) }
                            RelationshipSettings(container.repository)
                            UpdatePanel(container.updates)
                        }
                    }
                }
            }
        }
    }
}
