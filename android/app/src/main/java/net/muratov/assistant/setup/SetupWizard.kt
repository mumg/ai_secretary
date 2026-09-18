package net.muratov.assistant.setup

import android.app.Activity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
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
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import net.muratov.assistant.security.AppClientIdentity

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
    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        val payload = result.contents
        if (payload != null) {
            busy = true
            error = null
            scope.launch {
                try {
                    val imported = withContext(Dispatchers.IO) { AppClientIdentity.importQR(activity, payload) }
                    url = imported.server
                    alias = imported.alias
                    step = 2
                } catch (cancelled: CancellationException) { throw cancelled
                } catch (_: Exception) {
                    error = "Не удалось прочитать ключ. Используйте QR-код из настроек AI Секретаря с действующим сертификатом."
                } finally { busy = false }
            }
        }
    }
    val scan: () -> Unit = {
        scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            .setPrompt("Наведите камеру на QR-код в настройках AI Секретаря")
            .setBeepEnabled(false).setBarcodeImageEnabled(false).setOrientationLocked(false)
            .setCaptureActivity(IdentityCaptureActivity::class.java))
    }
    val certificateLabel = when {
        AppClientIdentity.isAppAlias(alias) -> "Ключ сохранён в приложении"
        alias != null -> "Ранее выбранный сертификат Android"
        else -> "Без клиентского сертификата"
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
                    Button(enabled = !busy, onClick = scan) { Text("Сканировать QR-код") }
                    Text("Введите HTTPS-адрес вашей установки. Его можно получить у администратора AI Секретаря.")
                    OutlinedTextField(url, { url = it; error = null }, modifier = Modifier.fillMaxWidth(),
                        label = { Text("Адрес сервера") }, placeholder = { Text("https://assistant.example.org") },
                        singleLine = true, isError = url.isNotBlank() && normalized == null,
                        supportingText = { Text("HTTPS, без пути, логина и параметров. Допускается порт.") })
                }
                1 -> {
                    Text("Ключ доступа")
                    Text("Отсканируйте QR-код из раздела «Мобильное приложение» в настройках сервера. Ключ останется в закрытом каталоге приложения и не будет установлен в систему. Для сервера без mTLS этот шаг можно пропустить.")
                    Text(certificateLabel)
                    Button(enabled = !busy, onClick = scan) { Text("Сканировать QR-код") }
                    if (alias != null) TextButton(enabled = !busy, onClick = { alias = null }) { Text("Подключаться без сертификата") }
                }
                2 -> {
                    Text("Проверка подключения")
                    Text(normalized.orEmpty())
                    Text(certificateLabel)
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
                            withContext(Dispatchers.IO) {
                                settings.saveConnection(normalized!!, alias)
                                runCatching { AppClientIdentity.retainOnly(activity, alias) }
                            }
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
