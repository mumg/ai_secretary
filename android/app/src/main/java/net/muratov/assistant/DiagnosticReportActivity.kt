package net.muratov.assistant

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
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
import net.muratov.assistant.data.remote.DiagnosticDraftDto
import net.muratov.assistant.data.remote.DiagnosticDraftRequest
import net.muratov.assistant.data.remote.DiagnosticEditDto
import net.muratov.assistant.data.remote.DiagnosticFieldDto
import net.muratov.assistant.data.remote.DiagnosticReportStatusDto
import net.muratov.assistant.data.remote.DiagnosticReplaceAllDto
import net.muratov.assistant.i18n.tr
import net.muratov.assistant.ui.ImproverTheme

@OptIn(ExperimentalMaterial3Api::class)
class DiagnosticReportActivity : net.muratov.assistant.i18n.LocalizedActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val resumeId = intent.getStringExtra("draft_id")
        val originKind = intent.getStringExtra("origin_kind") ?: if (resumeId != null) "" else return finish()
        val originId = intent.getStringExtra("origin_id") ?: if (resumeId != null) "" else return finish()
        val repository = (application as ImproverApplication).container.repository
        setContent { ImproverTheme {
            var issue by remember { mutableStateOf(if (originKind == "task") "false_task" else if (originKind == "delegation") "false_delegation" else "missing_task") }
            var expected by remember { mutableStateOf("") }
            var comment by remember { mutableStateOf("") }
            var draft by remember { mutableStateOf<DiagnosticDraftDto?>(null) }
            var editedFields by remember { mutableStateOf(mapOf<String, String>()) }
            var findAll by remember { mutableStateOf("") }
            var replaceAll by remember { mutableStateOf("") }
            var error by remember { mutableStateOf("") }
            var consent by remember { mutableStateOf(false) }
            var sentStatus by remember { mutableStateOf<DiagnosticReportStatusDto?>(null) }
            var busy by remember { mutableStateOf(false) }
            val scope = rememberCoroutineScope()
            LaunchedEffect(resumeId) {
                if (resumeId != null) {
                    try {
                        draft = repository.diagnosticDraft(resumeId)
                        editedFields = draft!!.fields.associate { it.path to it.value }
                    } catch (e: CancellationException) { throw e }
                    catch (e: Exception) { error = e.message ?: tr("Не удалось загрузить черновик") }
                }
            }
            fun close() { finish() }
            Scaffold(topBar = {
                TopAppBar(title = { Text(tr("Сообщить об ошибке")) }, navigationIcon = {
                    TextButton(onClick = ::close) { Text(tr("Закрыть")) }
                })
            }) { padding ->
                Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    if (resumeId == null && draft == null) {
                    Text(tr("Выберите ошибку анализа"), style = MaterialTheme.typography.titleMedium)
                    val options = listOf(
                        "false_task" to tr("Задача создана ошибочно"),
                        "false_delegation" to tr("Поручение создано ошибочно"),
                        "missing_task" to tr("Задача не создана"),
                        "missing_delegation" to tr("Поручение не создано"),
                        "wrong_assignee" to tr("Неверный исполнитель"),
                        "wrong_content" to tr("Неверное содержание"),
                        "other" to tr("Другая ошибка"),
                    )
                    options.forEach { (value, title) ->
                        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                            RadioButton(selected = issue == value, onClick = { issue = value })
                            TextButton(onClick = { issue = value }) { Text(title) }
                        }
                    }
                    OutlinedTextField(expected, { expected = it.take(4000) }, label = { Text(tr("Ожидаемый результат")) }, modifier = Modifier.fillMaxWidth(), minLines = 2)
                    OutlinedTextField(comment, { comment = it.take(4000) }, label = { Text(tr("Комментарий")) }, modifier = Modifier.fillMaxWidth(), minLines = 2)
                    if (error.isNotEmpty()) Text(error, color = MaterialTheme.colorScheme.error)
                    Button(onClick = {
                        scope.launch {
                            busy = true; error = ""
                            try {
                                draft = repository.createDiagnosticDraft(DiagnosticDraftRequest(originKind, originId, issue, comment, expected))
                                editedFields = draft!!.fields.associate { it.path to it.value }
                                consent = false
                                if (draft!!.state == "processing") {
                                    Toast.makeText(this@DiagnosticReportActivity, tr("Обезличивание продолжается в фоне. Вернитесь в «Обращения», когда черновик будет готов."), Toast.LENGTH_LONG).show()
                                    startActivity(Intent(this@DiagnosticReportActivity, DiagnosticReportsActivity::class.java))
                                    finish()
                                }
                            } catch (e: CancellationException) { throw e }
                            catch (e: Exception) { error = e.message ?: tr("Не удалось сформировать отчёт") }
                            finally { busy = false }
                        }
                    }, enabled = !busy) { Text(tr("Сформировать отчёт")) }
                    }
                    if (busy) CircularProgressIndicator()
                    draft?.takeIf { it.state == "ready" }?.let { current ->
                        current.warnings.forEach { Text(it, color = MaterialTheme.colorScheme.error) }
                        Text(tr("Проверьте обезличивание каждого поля"), style = MaterialTheme.typography.titleMedium)
                        current.fields.forEach { field ->
                            OutlinedTextField(
                                editedFields[field.path] ?: field.value,
                                { editedFields = editedFields + (field.path to it) },
                                label = { Text(diagnosticFieldLabel(field)) },
                                modifier = Modifier.fillMaxWidth(),
                                minLines = if (field.path.endsWith("/body")) 6 else 2,
                            )
                        }
                        OutlinedTextField(findAll, { findAll = it.take(200) }, label = { Text(tr("Найти во всех полях")) }, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(replaceAll, { replaceAll = it.take(200) }, label = { Text(tr("Заменить на")) }, modifier = Modifier.fillMaxWidth())
                        OutlinedButton(onClick = {
                            scope.launch {
                                busy = true; error = ""
                                try {
                                    draft = repository.editDiagnosticDraft(current.draftId, current.fields.map { DiagnosticEditDto(it.path, editedFields[it.path] ?: it.value) }, DiagnosticReplaceAllDto(findAll, replaceAll))
                                    editedFields = draft!!.fields.associate { it.path to it.value }
                                    findAll = ""; replaceAll = ""; consent = false
                                } catch (e: CancellationException) { throw e }
                                catch (e: Exception) { error = e.message ?: tr("Не удалось проверить правки") }
                                finally { busy = false }
                            }
                        }, enabled = !busy && findAll.length >= 2) { Text(tr("Заменить во всех полях")) }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = {
                                scope.launch {
                                    busy = true; error = ""
                                    try {
                                        draft = repository.editDiagnosticDraft(current.draftId, current.fields.map { DiagnosticEditDto(it.path, editedFields[it.path] ?: it.value) })
                                        editedFields = draft!!.fields.associate { it.path to it.value }
                                        consent = false
                                    }
                                    catch (e: CancellationException) { throw e }
                                    catch (e: Exception) { error = e.message ?: tr("Не удалось проверить правки") }
                                    finally { busy = false }
                                }
                            }, enabled = !busy && current.fields.any { editedFields[it.path] != it.value }) { Text(tr("Проверить правки")) }
                        }
                        Text(tr("Получатель: connect.ai-secretary.co. Отправятся только показанные обезличенные поля."))
                        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                            Checkbox(checked = consent, onCheckedChange = { consent = it })
                            Text(tr("Я проверил содержимое и согласен отправить отчёт"))
                        }
                        Button(onClick = {
                            scope.launch {
                                busy = true; error = ""
                                try {
                                    sentStatus = repository.sendDiagnosticDraft(current.draftId, current.payloadSha256)
                                    draft = null
                                } catch (e: CancellationException) { throw e }
                                catch (e: Exception) { error = e.message ?: tr("Не удалось отправить отчёт") }
                                finally { busy = false }
                            }
                        }, enabled = !busy && consent && current.fields.all { editedFields[it.path] == it.value }) { Text(tr("Отправить отчёт")) }
                    }
                    sentStatus?.let { status ->
                        Text(tr("Номер обращения: {0}", status.reportId), style = MaterialTheme.typography.titleMedium)
                        Text(diagnosticStatusLabel(status.state))
                        status.lastError?.takeIf(String::isNotBlank)?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                        Button(onClick = { startActivity(Intent(this@DiagnosticReportActivity, DiagnosticReportsActivity::class.java)) }) { Text(tr("Посмотреть обращения")) }
                    }
                }
            }
        } }
    }

    companion object {
        fun draftIntent(context: Context, id: String) =
            Intent(context, DiagnosticReportActivity::class.java).putExtra("draft_id", id)
        fun intent(context: Context, kind: String, id: String) =
            Intent(context, DiagnosticReportActivity::class.java)
                .putExtra("origin_kind", kind).putExtra("origin_id", id)
    }
}

internal fun diagnosticStatusLabel(state: String) = when (state) {
    "queued" -> tr("Ожидает отправки")
    "received" -> tr("Получено")
    "in_review" -> tr("На рассмотрении")
    "resolved" -> tr("Решено")
    "rejected" -> tr("Отклонено")
    else -> state
}

internal fun diagnosticFieldLabel(field: DiagnosticFieldDto): String {
    val parts = field.path.split('/')
    if (field.path == "/issue/user_comment") return tr("Комментарий")
    if (field.path == "/issue/expected") return tr("Ожидаемый результат")
    if (parts.size == 5 && parts[1] == "context" && parts[2] == "events") {
        val title = when (parts[4]) { "author" -> tr("Автор"); "subject" -> tr("Тема"); "body" -> tr("Текст"); else -> field.label }
        return tr("Сообщение {0}: {1}", (parts[3].toIntOrNull() ?: 0) + 1, title)
    }
    if (parts.size == 5 && parts[1] == "issue" && parts[2] == "observed") {
        val title = when (parts[4]) { "title" -> tr("Название"); "evidence" -> tr("Основание"); "assignee_name" -> tr("Исполнитель"); "assignee_email" -> tr("Email исполнителя"); else -> field.label }
        return tr("Найденный элемент {0}: {1}", (parts[3].toIntOrNull() ?: 0) + 1, title)
    }
    return field.label
}
