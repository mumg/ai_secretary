package net.muratov.assistant.updates

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.activity.compose.LocalActivity
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import net.muratov.assistant.BuildConfig

@Composable
fun UpdatePrompt(updates: AppUpdates) {
    val state by updates.state.collectAsState()
    var dismissed by rememberSaveable { mutableLongStateOf(0) }
    LaunchedEffect(updates) { updates.check() }
    val release = state.release
    if (state.ready && release != null && dismissed != release.versionCode) {
        AlertDialog(onDismissRequest = { dismissed = release.versionCode },
            title = { Text("Доступна версия ${release.versionName}") },
            text = { Text("Обновление скачано и проверено. Android предложит подтвердить установку. Настройки и данные сохранятся.") },
            confirmButton = { InstallButton(updates) },
            dismissButton = { TextButton(onClick = { dismissed = release.versionCode }) { Text("Позже") } })
    }
}

@Composable
fun UpdatePanel(updates: AppUpdates) {
    val state by updates.state.collectAsState()
    val scope = rememberCoroutineScope()
    var automatic by remember { mutableStateOf(updates.automatic) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Версия ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
        Row {
            Switch(checked = automatic, onCheckedChange = {
                automatic = it; updates.automatic = it
                if (it) scope.launch { updates.check(force = true) }
            })
            Text("Автоматически скачивать обновления по Wi-Fi", Modifier.padding(start = 8.dp))
        }
        Text("Проверка при запуске и каждые 12 часов. Установку нужно подтвердить в Android.", style = MaterialTheme.typography.bodySmall)
        state.message?.let { Text(it) }
        if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        OutlinedButton(enabled = !state.busy, onClick = { scope.launch { updates.check(force = true) } }) { Text("Проверить обновления") }
        if (state.release != null && !state.ready) {
            Button(enabled = !state.busy, onClick = { scope.launch { updates.download() } }) { Text("Скачать обновление") }
        }
        if (state.ready) InstallButton(updates)
    }
}

@Composable
private fun InstallButton(updates: AppUpdates) {
    val activity = checkNotNull(LocalActivity.current)
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    fun install() {
        if (busy) return
        scope.launch {
            busy = true; error = null
            try { activity.startActivity(updates.installIntent()) }
            catch (failure: Exception) { error = "Не удалось открыть установщик: ${failure.message.orEmpty().take(160)}" }
            finally { busy = false }
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (activity.packageManager.canRequestPackageInstalls()) install()
        else error = "Разрешите установку обновлений для AI Секретаря и нажмите «Установить» ещё раз."
    }
    Column {
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(enabled = !busy, onClick = {
            if (activity.packageManager.canRequestPackageInstalls()) install()
            else runCatching {
                permission.launch(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}")))
            }.onFailure { error = "Откройте настройки Android и разрешите установку из этого приложения." }
        }) { Text(if (busy) "Проверяем APK…" else "Установить") }
    }
}
