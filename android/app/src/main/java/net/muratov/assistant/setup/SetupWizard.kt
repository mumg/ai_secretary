package net.muratov.assistant.setup

import net.muratov.assistant.i18n.tr

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
import net.muratov.assistant.security.GatewayIdentity

@Composable
fun SetupWizard(
    settings: SettingsStore,
    onComplete: () -> Unit,
    onCancel: (() -> Unit)? = null,
    checkConnection: (suspend (String, String?) -> Unit)? = null,
) {
    val activity = checkNotNull(LocalActivity.current)
    var step by rememberSaveable { mutableIntStateOf(0) }
    var url by rememberSaveable { mutableStateOf("") }
    var alias by rememberSaveable { mutableStateOf<String?>(null) }
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
                    step = 1
                } catch (cancelled: CancellationException) { throw cancelled
                } catch (_: Exception) {
                    error = tr("Не удалось прочитать ключ. Используйте действующий QR-код прямого подключения или гейтвея из настроек AI Секретаря.")
                } finally { busy = false }
            }
        }
    }
    val scan: () -> Unit = {
        scanner.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            .setPrompt(tr("Наведите камеру на QR-код в настройках AI Секретаря"))
            .setBeepEnabled(false).setBarcodeImageEnabled(false).setOrientationLocked(false)
            .setCaptureActivity(IdentityCaptureActivity::class.java))
    }
    val certificateLabel = when {
        GatewayIdentity.isAlias(alias) -> tr("Через гейтвей · ключ сохранён в приложении")
        AppClientIdentity.isAppAlias(alias) -> tr("Ключ сохранён в приложении")
        else -> tr("QR-код не содержит клиентский сертификат")
    }
    BackHandler(enabled = step > 0 && !busy) { step--; error = null }
    Surface(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().systemBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp)) {
            net.muratov.assistant.i18n.LanguageSetting()
            Text(tr("Настройка AI Секретаря"), style = MaterialTheme.typography.headlineMedium)
            when (step) {
                0 -> {
                    Text(tr("Подключите свой сервер"))
                    Text(tr("Считайте QR-код из раздела удалённого подключения в настройках сервера. Ключ будет храниться только в защищённом хранилище этого устройства."))
                    Button(enabled = !busy, onClick = scan) { Text(tr("Сканировать QR-код")) }
                }
                1 -> {
                    Text(tr("Проверка подключения"))
                    Text(normalized.orEmpty())
                    Text(certificateLabel)
                    Text(tr("Проверим доступ к API сервера, затем сохраним настройки и откроем приложение."))
                }
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (step == 1) Button(modifier = Modifier.fillMaxWidth(), enabled = !busy && normalized != null && (AppClientIdentity.isAppAlias(alias) || GatewayIdentity.isAlias(alias)), onClick = {
                    busy = true; error = null
                    scope.launch {
                        try {
                            if (checkConnection != null) checkConnection(normalized!!, alias)
                            else checkServerConnection(activity, normalized!!, alias)
                            withContext(Dispatchers.IO) {
                                settings.saveConnection(normalized, alias)
                                runCatching { AppClientIdentity.retainOnly(activity, alias) }
                            }
                            onComplete()
                        } catch (cancelled: CancellationException) { throw cancelled
                        } catch (failure: Exception) {
                            error = tr("Не удалось подключиться. Проверьте адрес, сеть и сертификат. {0}" , failure.message.orEmpty().take(160))
                        } finally { busy = false }
                    }
            }) { Text(tr("Проверить и начать")) }
            Row {
                if (step > 0) TextButton(enabled = !busy, onClick = { step--; error = null }) { Text(tr("Назад")) }
                onCancel?.let { cancel -> TextButton(enabled = !busy, onClick = cancel) { Text(tr("Отмена")) } }
            }
        }
    }
}

private suspend fun checkServerConnection(activity: Activity, url: String, alias: String?) =
    withContext(Dispatchers.IO) {
        require(AppClientIdentity.isAppAlias(alias) || GatewayIdentity.isAlias(alias)) {
            tr("QR-код не содержит клиентский сертификат")
        }
        val client = ApiFactory.client(activity, url, alias).newBuilder()
            .callTimeout(25, TimeUnit.SECONDS).followRedirects(false).build()
        try {
            client.newCall(okhttp3.Request.Builder().url("${url}/api/v1/system/status").build())
                .execute().use { response ->
                    if (!response.isSuccessful) throw java.io.IOException("HTTP ${response.code}")
                    val source = response.body.source()
                    source.request(262_145)
                    require(source.buffer.size <= 262_144) { tr("Некорректный ответ сервера") }
                    val status = org.json.JSONObject(source.buffer.readUtf8())
                    require(status.has("overall_status") && status.optJSONArray("components") != null) {
                        tr("Сервер не вернул статус AI Секретаря")
                    }
                }
        } finally {
            client.connectionPool.evictAll()
            client.dispatcher.executorService.shutdown()
        }
    }
