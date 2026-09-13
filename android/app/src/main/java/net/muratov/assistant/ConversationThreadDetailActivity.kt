package net.muratov.assistant

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import net.muratov.assistant.data.remote.ConversationEventDto
import net.muratov.assistant.data.remote.ConversationThreadDetailDto
import net.muratov.assistant.ui.ConversationThreadDetailUiState
import net.muratov.assistant.ui.ConversationThreadDetailViewModel
import net.muratov.assistant.ui.ImproverTheme
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class ConversationThreadDetailActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val threadId = intent.getStringExtra(EXTRA_THREAD_ID) ?: return finish()
        val application = application as ImproverApplication
        setContent {
            ImproverTheme {
                val detailViewModel: ConversationThreadDetailViewModel = viewModel(
                    factory = ConversationThreadDetailViewModel.Factory(
                        application.container.repository,
                        threadId,
                    ),
                )
                val state by detailViewModel.state.collectAsState()
                ConversationThreadDetailScreen(
                    state = state,
                    onBack = ::finish,
                    onRetry = detailViewModel::load,
                    onOpenEvent = { eventId, sourceLabel ->
                        startActivity(EventDetailActivity.intent(this, eventId, sourceLabel))
                    },
                )
            }
        }
    }

    companion object {
        private const val EXTRA_THREAD_ID = "thread_id"

        fun intent(context: Context, threadId: String): Intent =
            Intent(context, ConversationThreadDetailActivity::class.java)
                .putExtra(EXTRA_THREAD_ID, threadId)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ConversationThreadDetailScreen(
    state: ConversationThreadDetailUiState,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onOpenEvent: (String, String) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        state.detail?.title ?: "Переписка",
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

            state.detail != null -> ConversationThreadDetailContent(
                detail = state.detail,
                onOpenEvent = onOpenEvent,
                modifier = Modifier.padding(padding),
            )
        }
    }
}

@Composable
private fun ConversationThreadDetailContent(
    detail: ConversationThreadDetailDto,
    onOpenEvent: (String, String) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Column(
                Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 6.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                DetailField("Источник", detail.sourceLabel)
                DetailField(
                    "Период",
                    "${formatThreadDate(detail.firstEventAt)} — " +
                        formatThreadDate(detail.lastEventAt),
                )
                participantNames(detail.participants).takeIf(String::isNotBlank)?.let {
                    DetailField("Участники", it)
                }
                Text("Резюме Qwen", style = MaterialTheme.typography.titleMedium)
                SelectionContainer {
                    Text(
                        detail.summary?.takeIf(String::isNotBlank)
                            ?: "Резюме переписки пока не сформировано",
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                Text(
                    "Сообщения (${detail.eventCount})",
                    style = MaterialTheme.typography.titleMedium,
                )
                if (detail.hasMoreEvents) {
                    Text(
                        "Показаны 100 последних сообщений",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        items(detail.events, key = ConversationEventDto::id) { event ->
            ConversationEventCard(event) {
                onOpenEvent(event.id, detail.sourceLabel)
            }
        }
        item { Box(Modifier.padding(bottom = 16.dp)) }
    }
}

@Composable
private fun ConversationEventCard(
    event: ConversationEventDto,
    onOpen: () -> Unit,
) {
    Card(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (event.direction == "OUTGOING") {
                Color(0xFF1D2A31)
            } else {
                MaterialTheme.colorScheme.surfaceVariant
            },
        ),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.45f)),
    ) {
        Column(
            Modifier.fillMaxWidth().padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            Text(
                event.subject?.takeIf(String::isNotBlank) ?: "Сообщение без темы",
                style = MaterialTheme.typography.titleSmall,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                "${directionLabel(event.direction)} • ${formatThreadDate(event.occurredAt)}",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            event.author?.takeIf(String::isNotBlank)?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Text(
                event.preview.ifBlank { "Текст сообщения пуст" },
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 6,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                "Открыть оригинал",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

@Composable
private fun DetailField(label: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium)
        SelectionContainer { Text(value, modifier = Modifier.fillMaxWidth()) }
    }
}

private fun participantNames(participants: List<Map<String, String>>): String = participants
    .mapNotNull { it["name"]?.takeIf(String::isNotBlank) ?: it["address"] }
    .filter(String::isNotBlank)
    .distinct()
    .joinToString(", ")

private fun directionLabel(direction: String): String = when (direction) {
    "OUTGOING" -> "Исходящее"
    "INCOMING" -> "Входящее"
    else -> direction
}

private val conversationDateFormatter = DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm")

private fun formatThreadDate(value: String): String = runCatching {
    OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.systemDefault())
        .format(conversationDateFormatter)
}.getOrDefault(value)
