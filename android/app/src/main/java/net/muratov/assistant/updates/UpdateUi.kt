package net.muratov.assistant.updates

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import net.muratov.assistant.BuildConfig
import net.muratov.assistant.i18n.tr

@Composable
fun UpdatePrompt(updates: AppUpdates) {
    val state by updates.state.collectAsState()
    var dismissed by remember { mutableStateOf(false) }
    val context = LocalContext.current
    LaunchedEffect(updates) { updates.check() }
    if (state.available && !dismissed) {
        AlertDialog(onDismissRequest = { dismissed = true },
            title = { Text(tr("Доступно обновление приложения")) },
            text = { Text(state.message.orEmpty()) },
            confirmButton = { TextButton(onClick = { dismissed = true; updates.store.open(context) }) {
                Text(tr("Открыть {0}", updates.store.title))
            } },
            dismissButton = { TextButton(onClick = { dismissed = true }) { Text(tr("Позже")) } })
    }
}

@Composable
fun UpdatePanel(updates: AppUpdates) {
    val state by updates.state.collectAsState()
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(tr("Версия {0} ({1})", BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE))
        Text(tr("Обновления устанавливаются через {0}.", updates.store.title),
            style = MaterialTheme.typography.bodySmall)
        state.message?.let { Text(it) }
        if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        OutlinedButton(enabled = !state.busy, onClick = { scope.launch { updates.check(force = true) } }) {
            Text(tr("Проверить обновления"))
        }
        if (state.available) Button(onClick = { updates.store.open(context) }) {
            Text(tr("Открыть {0}", updates.store.title))
        }
    }
}
