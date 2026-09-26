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
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import net.muratov.assistant.data.remote.MeetingResultDetailDto
import net.muratov.assistant.data.remote.ParticipantMeetingSummaryDto
import net.muratov.assistant.ui.ImproverTheme
import net.muratov.assistant.ui.MarkdownText
import net.muratov.assistant.ui.MeetingResultDetailUiState
import net.muratov.assistant.ui.MeetingResultDetailViewModel
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

class MeetingResultDetailActivity : net.muratov.assistant.i18n.LocalizedActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val resultId = intent.getStringExtra(EXTRA_RESULT_ID) ?: return finish()
        val application = application as ImproverApplication
        setContent {
            ImproverTheme {
                val detailViewModel: MeetingResultDetailViewModel = viewModel(
                    factory = MeetingResultDetailViewModel.Factory(
                        application.container.repository,
                        resultId,
                    ),
                )
                val state by detailViewModel.state.collectAsState()
                MeetingResultDetailScreen(
                    state = state,
                    onBack = ::finish,
                    onRetry = detailViewModel::load,
                    onOpenSource = { eventId, sourceLabel ->
                        startActivity(EventDetailActivity.intent(this, eventId, sourceLabel))
                    },
                )
            }
        }
    }

    companion object {
        private const val EXTRA_RESULT_ID = "meeting_result_id"

        fun intent(context: Context, resultId: String): Intent =
            Intent(context, MeetingResultDetailActivity::class.java)
                .putExtra(EXTRA_RESULT_ID, resultId)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MeetingResultDetailScreen(
    state: MeetingResultDetailUiState,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onOpenSource: (String, String) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        state.detail?.title ?: tr("Результат встречи"),
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
            ) { CircularProgressIndicator() }

            state.error != null -> Column(
                Modifier.fillMaxSize().padding(padding).padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Text(state.error, color = MaterialTheme.colorScheme.error)
                Button(onClick = onRetry) { Text(tr("Повторить")) }
            }

            state.detail != null -> MeetingResultDetailContent(
                detail = state.detail,
                onOpenSource = onOpenSource,
                modifier = Modifier.padding(padding),
            )
        }
    }
}

@Composable
private fun MeetingResultDetailContent(
    detail: MeetingResultDetailDto,
    onOpenSource: (String, String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val uriHandler = LocalUriHandler.current
    Column(
        modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        ResultField(
            tr("Когда"),
            formatMeetingResultPeriod(
                detail.startsAt,
                detail.endsAt,
                detail.originType,
                detail.calendarMeetingId,
            ),
        )
        ResultField(tr("Тема"), detail.title)
        participantNames(detail.participants).takeIf(String::isNotBlank)?.let {
            ResultField(tr("Участники"), it)
        }
        detail.meetingUrl?.takeIf(String::isNotBlank)?.let { url ->
            TextButton(onClick = { runCatching { uriHandler.openUri(url) } }) {
                Text(tr("Открыть встречу"))
            }
        }

        Text(tr("Автоматическое резюме"), style = MaterialTheme.typography.titleMedium)
        MarkdownText(
                detail.summary?.takeIf(String::isNotBlank)
                    ?: if (detail.analysisState == "COMPLETED") {
                        tr("Резюме не сформировано")
                    } else {
                        tr("Qwen анализирует материалы встречи…")
                    },
                modifier = Modifier.fillMaxWidth(),
        )
        ResultList(tr("Решения"), detail.decisions)
        ResultList(tr("Основные договорённости"), detail.agreements)

        Text(tr("Резюме участников"), style = MaterialTheme.typography.titleMedium)
        if (detail.participantSummaries.isEmpty()) {
            Text(
                tr("Дополнительных итогов от участников пока нет"),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            detail.participantSummaries.forEach { item ->
                ParticipantSummaryCard(item) {
                    onOpenSource(item.sourceEventId, item.sourceLabel)
                }
            }
        }
        TextButton(
            onClick = { onOpenSource(detail.sourceEventId, detail.sourceLabel) },
        ) {
            Text(
                if (detail.originType == "mts_transcript") {
                    tr("Открыть расшифровку")
                } else {
                    tr("Открыть исходное письмо")
                },
            )
        }
    }
}

@Composable
private fun ParticipantSummaryCard(
    item: ParticipantMeetingSummaryDto,
    onOpen: () -> Unit,
) {
    Card(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF202725)),
        border = BorderStroke(1.dp, Color(0xFF4D7164)),
    ) {
        Column(
            Modifier.fillMaxWidth().padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                item.author?.takeIf(String::isNotBlank) ?: tr("Участник"),
                style = MaterialTheme.typography.titleSmall,
            )
            Text(
                "${formatResultInstant(item.occurredAt)} · ${item.sourceLabel}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(item.summary, style = MaterialTheme.typography.bodyMedium)
            if (item.agreements.isNotEmpty()) {
                Text(
                    tr("Договорённости: {0}" , item.agreements.joinToString(" • ")),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
private fun ResultField(label: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium)
        SelectionContainer { Text(value, modifier = Modifier.fillMaxWidth()) }
    }
}

@Composable
private fun ResultList(label: String, values: List<String>) {
    if (values.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(label, style = MaterialTheme.typography.titleSmall)
        MarkdownText(values.joinToString("\n") { "- ${it}" }, Modifier.fillMaxWidth())
    }
}

private fun participantNames(participants: List<Map<String, String>>): String =
    participants.mapNotNull { participant ->
        participant["name"]?.takeIf(String::isNotBlank)
            ?: participant["address"]?.takeIf(String::isNotBlank)
    }.distinct().joinToString(", ")

private fun formatResultInstant(value: String): String = runCatching {
    OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.systemDefault()).format(
        net.muratov.assistant.i18n.dateTimeFormatter(),
    )
}.getOrDefault(value)
