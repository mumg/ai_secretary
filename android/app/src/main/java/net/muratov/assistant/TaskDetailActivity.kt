package net.muratov.assistant

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
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

class TaskDetailActivity : ComponentActivity() {
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
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = state.detail?.task?.title ?: "Задача",
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Назад")
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
                Button(onClick = onRetry) { Text("Повторить") }
            }

            state.detail != null -> TaskDetailContent(
                detail = state.detail,
                modifier = Modifier.padding(padding),
                onReject = onReject,
            )
        }
    }
}

@Composable
private fun TaskDetailContent(
    detail: TaskDetailDto,
    modifier: Modifier = Modifier,
    onReject: () -> Unit,
) {
    val source = detail.source
    val uriHandler = LocalUriHandler.current
    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        if (detail.task.status in setOf("NEW", "IN_PROGRESS", "POSSIBLY_COMPLETED", "NEEDS_CONFIRMATION")) {
            TextButton(onClick = onReject) { Text("Отказаться от задачи") }
        } else if (detail.task.status == "CANCELLED") {
            Text("Задача отменена", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (source == null) {
            DetailField("Источник", "Задача добавлена вручную")
        } else {
            DetailField("Источник", source.sourceLabel)
            val (eventDate, eventTime) = eventDateTime(source.occurredAt)
            DetailField("Дата", eventDate)
            DetailField("Время", eventTime)
            source.subject?.takeIf(String::isNotBlank)?.let {
                DetailField("Тема", it)
            }
        }

        DetailField("Краткая выжимка", briefSummary(detail))

        source?.let {
            DetailField("Тип", sourceTypeLabel(it.sourceType, it.eventType))
            it.author?.takeIf(String::isNotBlank)?.let { author ->
                DetailField("Отправитель", author)
            }
            val participants = participantText(it.participants)
            if (participants.isNotEmpty()) DetailField("Участники", participants)
            it.sourceUrl?.takeIf(String::isNotBlank)?.let { url ->
                TextButton(onClick = { runCatching { uriHandler.openUri(url) } }) {
                    Text("Открыть в источнике")
                }
            }
            Text("Оригинал сообщения", style = MaterialTheme.typography.titleMedium)
            LinkedMessageText(
                it.body.ifBlank { "Текст сообщения пуст" },
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
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
    "imap" -> "Письмо · IMAP"
    "exchange" -> "Письмо · Exchange"
    "mts_link" -> if (eventType == "meeting_transcript") {
        "Расшифровка встречи · МТС Линк"
    } else {
        "Сообщение · МТС Линк"
    }
    else -> "$eventType · $sourceType"
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
            name.isNotEmpty() && address.isNotEmpty() -> "$name <$address>"
            address.isNotEmpty() -> address
            else -> name
        }
    }.trim().trim(',')

private fun eventDateTime(value: String): Pair<String, String> = runCatching {
    val local = OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.systemDefault())
    val locale = Locale.forLanguageTag("ru-RU")
    local.format(DateTimeFormatter.ofPattern("dd.MM.yyyy", locale)) to
        local.format(DateTimeFormatter.ofPattern("HH:mm", locale))
}.getOrElse { value to "—" }
