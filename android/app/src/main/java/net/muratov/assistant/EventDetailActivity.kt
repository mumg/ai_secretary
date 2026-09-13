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
import net.muratov.assistant.data.remote.EventDto
import net.muratov.assistant.ui.EventDetailUiState
import net.muratov.assistant.ui.EventDetailViewModel
import net.muratov.assistant.ui.ImproverTheme
import net.muratov.assistant.ui.LinkedMessageText
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class EventDetailActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val eventId = intent.getStringExtra(EXTRA_EVENT_ID) ?: return finish()
        val sourceLabel = intent.getStringExtra(EXTRA_SOURCE_LABEL)
        val application = application as ImproverApplication
        setContent {
            ImproverTheme {
                val detailViewModel: EventDetailViewModel = viewModel(
                    factory = EventDetailViewModel.Factory(
                        application.container.repository,
                        eventId,
                    ),
                )
                val state by detailViewModel.state.collectAsState()
                EventDetailScreen(state, sourceLabel, ::finish, detailViewModel::load)
            }
        }
    }

    companion object {
        private const val EXTRA_EVENT_ID = "event_id"
        private const val EXTRA_SOURCE_LABEL = "source_label"

        fun intent(context: Context, eventId: String, sourceLabel: String?): Intent =
            Intent(context, EventDetailActivity::class.java)
                .putExtra(EXTRA_EVENT_ID, eventId)
                .putExtra(EXTRA_SOURCE_LABEL, sourceLabel)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EventDetailScreen(
    state: EventDetailUiState,
    sourceLabel: String?,
    onBack: () -> Unit,
    onRetry: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        state.event?.subject ?: "Источник",
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
            ) { CircularProgressIndicator() }

            state.error != null -> Column(
                Modifier.fillMaxSize().padding(padding).padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Text(state.error, color = MaterialTheme.colorScheme.error)
                Button(onClick = onRetry) { Text("Повторить") }
            }

            state.event != null -> EventDetailContent(
                event = state.event,
                sourceLabel = sourceLabel,
                modifier = Modifier.padding(padding),
            )
        }
    }
}

@Composable
private fun EventDetailContent(
    event: EventDto,
    sourceLabel: String?,
    modifier: Modifier = Modifier,
) {
    val uriHandler = LocalUriHandler.current
    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        EventField("Источник", sourceLabel ?: event.sourceId)
        EventField("Дата и время", formatEventDate(event.occurredAt))
        event.subject?.takeIf(String::isNotBlank)?.let { EventField("Тема", it) }
        event.author?.takeIf(String::isNotBlank)?.let { EventField("Автор", it) }
        val participants = event.participants.joinToString(", ") {
            it["name"] ?: it["address"].orEmpty()
        }.trim().trim(',')
        if (participants.isNotBlank()) EventField("Участники", participants)
        EventField("Тип", "${event.sourceType} · ${event.eventType}")
        event.sourceUrl?.takeIf(String::isNotBlank)?.let { url ->
            TextButton(onClick = { uriHandler.openUri(url) }) { Text("Открыть в источнике") }
        }
        Text("Оригинал", style = MaterialTheme.typography.titleMedium)
        LinkedMessageText(
            event.body.ifBlank { "Текст сообщения пуст" },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun EventField(label: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium)
        SelectionContainer { Text(value, modifier = Modifier.fillMaxWidth()) }
    }
}

private fun formatEventDate(value: String): String = runCatching {
    OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm"))
}.getOrDefault(value)
