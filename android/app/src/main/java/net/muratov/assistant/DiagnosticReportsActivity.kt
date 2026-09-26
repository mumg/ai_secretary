package net.muratov.assistant

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import net.muratov.assistant.data.remote.DiagnosticDraftStatusDto
import net.muratov.assistant.data.remote.DiagnosticReportStatusDto
import net.muratov.assistant.i18n.tr
import net.muratov.assistant.ui.ImproverTheme

@OptIn(ExperimentalMaterial3Api::class)
class DiagnosticReportsActivity : net.muratov.assistant.i18n.LocalizedActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val repository = (application as ImproverApplication).container.repository
        setContent { ImproverTheme {
            var items by remember { mutableStateOf<List<DiagnosticReportStatusDto>>(emptyList()) }
            var drafts by remember { mutableStateOf<List<DiagnosticDraftStatusDto>>(emptyList()) }
            var error by remember { mutableStateOf("") }
            var working by remember { mutableStateOf(false) }
            val scope = rememberCoroutineScope()
            suspend fun load() {
                working = true; error = ""
                try { val page = repository.diagnosticReports(); items = page.items; drafts = page.drafts }
                catch (e: CancellationException) { throw e }
                catch (e: Exception) { error = e.message ?: tr("Не удалось загрузить обращения") }
                finally { working = false }
            }
            LaunchedEffect(Unit) { load() }
            LaunchedEffect(Unit) { while (true) { delay(10000); load() } }
            Scaffold(topBar = { TopAppBar(title = { Text(tr("Обращения")) }, navigationIcon = { TextButton(onClick = ::finish) { Text(tr("Назад")) } }) }) { padding ->
                Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Button(onClick = { scope.launch { load() } }, enabled = !working) { Text(tr("Обновить статусы")) }
                    if (working) CircularProgressIndicator()
                    if (error.isNotEmpty()) Text(error, color = MaterialTheme.colorScheme.error)
                    if (items.isEmpty() && drafts.isEmpty() && !working) Text(tr("Обращений пока нет"))
                    drafts.forEach { draft ->
                        Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = if (draft.state == "ready") MaterialTheme.colorScheme.tertiaryContainer else MaterialTheme.colorScheme.surfaceVariant)) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text(tr("Черновик обращения"), style = MaterialTheme.typography.titleSmall)
                                Text(when (draft.state) { "ready" -> tr("Обезличивание готово к проверке"); "processing" -> tr("Обезличивание выполняется в фоне"); else -> tr("Не удалось подготовить обезличивание") })
                                draft.lastError?.takeIf(String::isNotBlank)?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                                if (draft.state == "ready") Button(onClick = { startActivity(DiagnosticReportActivity.draftIntent(this@DiagnosticReportsActivity, draft.draftId)) }) { Text(tr("Продолжить проверку")) }
                                if (draft.state == "failed") TextButton(onClick = { scope.launch { try { repository.retryDiagnosticDraft(draft.draftId); load() } catch (e: Exception) { error = e.message ?: tr("Не удалось повторить попытку") } } }) { Text(tr("Повторить попытку")) }
                            }
                        }
                    }
                    items.forEach { report ->
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text(report.reportId, style = MaterialTheme.typography.titleSmall)
                                Text(diagnosticStatusLabel(report.state))
                                report.lastError?.takeIf(String::isNotBlank)?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    TextButton(onClick = { scope.launch {
                                        try {
                                            val refreshed = repository.diagnosticReport(report.reportId)
                                            items = items.map { if (it.reportId == refreshed.reportId) refreshed else it }
                                            error = ""
                                        } catch (e: CancellationException) { throw e }
                                        catch (e: Exception) { error = e.message ?: tr("Не удалось проверить статус") }
                                    } }) { Text(tr("Проверить статус")) }
                                    if (report.state == "queued") TextButton(onClick = { scope.launch {
                                        try {
                                            val retried = repository.retryDiagnosticReport(report.reportId)
                                            items = items.map { if (it.reportId == retried.reportId) retried else it }
                                            error = ""
                                        } catch (e: CancellationException) { throw e }
                                        catch (e: Exception) { error = e.message ?: tr("Не удалось повторить отправку") }
                                    } }) { Text(tr("Повторить отправку")) }
                                }
                            }
                        }
                    }
                }
            }
        } }
    }
}
