package net.muratov.assistant

import net.muratov.assistant.i18n.tr

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import net.muratov.assistant.data.remote.TaskDetailDto
import net.muratov.assistant.ui.ImproverTheme
import net.muratov.assistant.ui.LinkedMessageText
import net.muratov.assistant.ui.TaskDetailUiState
import net.muratov.assistant.ui.TaskDetailViewModel
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

class TaskDetailActivity : net.muratov.assistant.i18n.LocalizedActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val taskId = intent.getStringExtra(EXTRA_TASK_ID)
        if (taskId == null) {
            finish()
            return
        }
        val application = application as ImproverApplication
        setContent {
            ImproverTheme {
                val detailViewModel: TaskDetailViewModel = viewModel(
                    factory = TaskDetailViewModel.Factory(
                        repository = application.container.repository,
                        taskId = taskId,
                    ),
                )
                val state by detailViewModel.state.collectAsState()
                TaskDetailScreen(
                    state = state,
                    onBack = ::finish,
                    onRetry = detailViewModel::load,
                    onReject = detailViewModel::reject,
                    onUpdate = detailViewModel::update,
                )
            }
        }
    }

    companion object {
        private const val EXTRA_TASK_ID = "task_id"

        fun intent(context: Context, taskId: String): Intent =
            Intent(context, TaskDetailActivity::class.java).putExtra(EXTRA_TASK_ID, taskId)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TaskDetailScreen(
    state: TaskDetailUiState,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onReject: () -> Unit,
    onUpdate: (String, String?) -> Unit,
) {
    var editing by remember { mutableStateOf(false) }
    if (editing && state.detail != null) {
        EditTaskScheduleDialog(state.detail.task.priority, state.detail.task.dueAt,
            onDismiss = { editing = false },
            onSave = { priority, due -> editing = false; onUpdate(priority, due) })
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = state.detail?.task?.title ?: tr("Задача"),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = tr("Назад"))
                    }
                },
            )
        },
    ) { padding ->
        when {
            state.loading -> Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center,
            ) {
                CircularProgressIndicator()
            }

            state.error != null -> Column(
                Modifier.fillMaxSize().padding(padding).padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Text(state.error, color = MaterialTheme.colorScheme.error)
                Button(onClick = onRetry) { Text(tr("Повторить")) }
            }

            state.detail != null -> TaskDetailContent(
                detail = state.detail,
                modifier = Modifier.padding(padding),
                onReject = onReject,
                onEdit = { editing = true },
            )
        }
    }
}

@Composable
private fun TaskDetailContent(
    detail: TaskDetailDto,
    modifier: Modifier = Modifier,
    onReject: () -> Unit,
    onEdit: () -> Unit,
) {
    val source = detail.source
    val uriHandler = LocalUriHandler.current
    val canCancel = detail.task.status in setOf("NEW", "IN_PROGRESS", "POSSIBLY_COMPLETED", "NEEDS_CONFIRMATION")
    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        DetailField(tr("Приоритет"), taskPriorityLabel(detail.task.priority))
        DetailField(tr("Срок"), detail.task.dueAt?.let { eventDateTime(it).let { (date, time) -> "$date $time" } } ?: tr("Без срока"))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Button(
                onClick = onEdit,
                modifier = Modifier.weight(1f, fill = false),
                shape = RoundedCornerShape(12.dp),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
            ) {
                Text(tr("Изменить приоритет и срок"), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            if (canCancel) {
                Spacer(Modifier.width(8.dp))
                OutlinedButton(
                    onClick = onReject,
                    shape = RoundedCornerShape(12.dp),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.error),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                ) {
                    Text(tr("Отменить"))
                }
            }
        }
        if (detail.task.status == "CANCELLED") {
            Text(tr("Задача отменена"), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (source == null) {
            DetailField(tr("Источник"), tr("Задача добавлена вручную"))
        } else {
            DetailField(tr("Источник"), source.sourceLabel)
            val (eventDate, eventTime) = eventDateTime(source.occurredAt)
            DetailField(tr("Дата"), eventDate)
            DetailField(tr("Время"), eventTime)
            source.subject?.takeIf(String::isNotBlank)?.let {
                DetailField(tr("Тема"), it)
            }
        }

        DetailField(tr("Краткая выжимка"), briefSummary(detail))

        source?.let {
            DetailField(tr("Тип"), sourceTypeLabel(it.sourceType, it.eventType))
            it.author?.takeIf(String::isNotBlank)?.let { author ->
                DetailField(tr("Отправитель"), author)
            }
            val participants = participantText(it.participants)
            if (participants.isNotEmpty()) DetailField(tr("Участники"), participants)
            it.sourceUrl?.takeIf(String::isNotBlank)?.let { url ->
                TextButton(onClick = { runCatching { uriHandler.openUri(url) } }) {
                    Text(tr("Открыть в источнике"))
                }
            }
            Text(tr("Оригинал сообщения"), style = MaterialTheme.typography.titleMedium)
            LinkedMessageText(
                it.body.ifBlank { tr("Текст сообщения пуст") },
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

private fun taskPriorityLabel(value: String): String = when (value) {
    "LOW" -> tr("Низкий")
    "HIGH" -> tr("Высокий")
    "CRITICAL" -> tr("Критический")
    else -> tr("Обычный")
}

@Composable
private fun EditTaskScheduleDialog(
    initialPriority: String,
    initialDue: String?,
    onDismiss: () -> Unit,
    onSave: (String, String?) -> Unit,
) {
    var priority by remember(initialPriority) { mutableStateOf(initialPriority) }
    var due by remember(initialDue) { mutableStateOf(initialDue.orEmpty()) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(tr("Изменить задачу")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(tr("Приоритет"))
                listOf("LOW", "NORMAL", "HIGH", "CRITICAL").chunked(2).forEach { choices ->
                    androidx.compose.foundation.layout.Row {
                        choices.forEach { value ->
                            TextButton(onClick = { priority = value }) {
                                Text((if (priority == value) "✓ " else "") + taskPriorityLabel(value))
                            }
                        }
                    }
                }
                DateTimeField(tr("Срок"), due) { due = it }
            }
        },
        confirmButton = { Button(onClick = { onSave(priority, due.ifBlank { null }) }) { Text(tr("Сохранить")) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(tr("Отмена")) } },
    )
}

@Composable
private fun DetailField(label: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium)
        SelectionContainer {
            Text(value, modifier = Modifier.fillMaxWidth(), style = MaterialTheme.typography.bodyLarge)
        }
    }
}

private fun sourceTypeLabel(sourceType: String, eventType: String): String = when (sourceType) {
    "imap" -> tr("Письмо · IMAP")
    "exchange" -> tr("Письмо · Exchange")
    "mts_link" -> if (eventType == "meeting_transcript") {
        tr("Расшифровка встречи · МТС Линк")
    } else {
        tr("Сообщение · МТС Линк")
    }
    else -> "${eventType} · ${sourceType}"
}

private fun briefSummary(detail: TaskDetailDto): String {
    val text = detail.task.description?.takeIf(String::isNotBlank)
        ?: detail.task.evidence?.takeIf(String::isNotBlank)
        ?: detail.task.title
    val normalized = text.replace(Regex("\\s+"), " ").trim()
    return if (normalized.length <= 600) normalized else normalized.take(599).trimEnd() + "…"
}

private fun participantText(participants: List<Map<String, String>>): String =
    participants.joinToString(", ") { participant ->
        val name = participant["name"].orEmpty().trim()
        val address = participant["address"].orEmpty().trim()
        when {
            name.isNotEmpty() && address.isNotEmpty() -> "${name} <${address}>"
            address.isNotEmpty() -> address
            else -> name
        }
    }.trim().trim(',')

private fun eventDateTime(value: String): Pair<String, String> = runCatching {
    val local = OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.systemDefault())
    val locale = net.muratov.assistant.i18n.Language.locale
    local.format(net.muratov.assistant.i18n.dateFormatter()) to
        local.format(DateTimeFormatter.ofPattern("HH:mm", locale))
}.getOrElse { value to "—" }
