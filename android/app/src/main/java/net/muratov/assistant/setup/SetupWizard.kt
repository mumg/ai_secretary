package net.muratov.assistant.setup

import android.app.Activity
import android.security.KeyChain
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.activity.compose.LocalActivity
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import net.muratov.assistant.data.ApiFactory
import net.muratov.assistant.data.SettingsStore
import java.util.concurrent.TimeUnit

@Composable
fun SetupWizard(
    settings: SettingsStore,
    onComplete: () -> Unit,
    onCancel: (() -> Unit)? = null,
    checkConnection: (suspend (String, String?) -> Unit)? = null,
) {
    val activity = checkNotNull(LocalActivity.current)
    var step by rememberSaveable { mutableIntStateOf(0) }
    var url by rememberSaveable { mutableStateOf(settings.serverUrl) }
    var alias by rememberSaveable { mutableStateOf(settings.certificateAlias) }
    var busy by remember { mutableStateOf(false) }
    var error by rememberSaveable { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val normalized = normalizeServerUrl(url)
    val importCertificate = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        error = null
    }
    BackHandler(enabled = step > 0 && !busy) { step--; error = null }
    Surface(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().systemBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp)) {
            Text("Настройка AI Секретаря", style = MaterialTheme.typography.headlineMedium)
            Text("Шаг ${step + 1} из 3", style = MaterialTheme.typography.labelLarge)
            when (step) {
                0 -> {
                    Text("Подключите свой сервер")
                    Text("Введите HTTPS-адрес вашей установки. Его можно получить у администратора AI Секретаря.")
                    OutlinedTextField(url, { url = it; error = null }, modifier = Modifier.fillMaxWidth(),
                        label = { Text("Адрес сервера") }, placeholder = { Text("https://assistant.example.org") },
                        singleLine = true, isError = url.isNotBlank() && normalized == null,
                        supportingText = { Text("HTTPS, без пути, логина и параметров. Допускается порт.") })
                }
                1 -> {
                    Text("Сертификат доступа")
                    Text("Если сервер защищён клиентским сертификатом (mTLS), установите выданный администратором сертификат и выберите его. Для сервера без mTLS этот шаг можно пропустить.")
                    Text(alias?.let { "Выбран: $it" } ?: "Сертификат не выбран")
                    OutlinedButton(onClick = {
                        runCatching { importCertificate.launch(KeyChain.createInstallIntent()) }
                            .onFailure { error = "Откройте настройки Android → Безопасность → Установить сертификат." }
                    }) { Text("Установить сертификат") }
                    Button(onClick = {
                        KeyChain.choosePrivateKeyAlias(activity, { selected ->
                            activity.runOnUiThread { if (selected != null) { alias = selected; error = null } }
                        }, arrayOf("RSA", "EC"), null, java.net.URI(normalized!!).host, -1, alias)
                    }) { Text("Выбрать сертификат") }
                    if (alias != null) TextButton(onClick = { alias = null }) { Text("Подключаться без сертификата") }
                }
                2 -> {
                    Text("Проверка подключения")
                    Text(normalized.orEmpty())
                    Text(alias?.let { "Сертификат: $it" } ?: "Без клиентского сертификата")
                    Text("Проверим доступ к API сервера, затем сохраним настройки и откроем приложение.")
                }
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            Button(modifier = Modifier.fillMaxWidth(), enabled = !busy && normalized != null, onClick = {
                if (step < 2) { step++; error = null } else {
                    busy = true; error = null
                    scope.launch {
                        try {
                            if (checkConnection != null) checkConnection(normalized!!, alias)
                            else checkServerConnection(activity, normalized!!, alias)
                            settings.saveConnection(normalized!!, alias)
                            onComplete()
                        } catch (cancelled: CancellationException) { throw cancelled
                        } catch (failure: Exception) {
                            error = "Не удалось подключиться. Проверьте адрес, сеть и сертификат. ${failure.message.orEmpty().take(160)}"
                        } finally { busy = false }
                    }
                }
            }) { Text(if (step < 2) "Далее" else "Проверить и начать") }
            Row {
                if (step > 0) TextButton(enabled = !busy, onClick = { step--; error = null }) { Text("Назад") }
                onCancel?.let { cancel -> TextButton(enabled = !busy, onClick = cancel) { Text("Отмена") } }
            }
        }
    }
}

private suspend fun checkServerConnection(activity: Activity, url: String, alias: String?) =
    withContext(Dispatchers.IO) {
        val client = ApiFactory.client(activity, url, alias).newBuilder()
            .callTimeout(25, TimeUnit.SECONDS).followRedirects(false).build()
        try {
            client.newCall(okhttp3.Request.Builder().url("$url/api/v1/system/status").build())
                .execute().use { response ->
                    if (!response.isSuccessful) throw java.io.IOException("HTTP ${response.code}")
                    val source = response.body.source()
                    source.request(262_145)
                    require(source.buffer.size <= 262_144) { "Некорректный ответ сервера" }
                    val status = org.json.JSONObject(source.buffer.readUtf8())
                    require(status.has("overall_status") && status.optJSONArray("components") != null) {
                        "Сервер не вернул статус AI Секретаря"
                    }
                }
        } finally {
            client.connectionPool.evictAll()
            client.dispatcher.executorService.shutdown()
        }
    }
