package net.muratov.assistant

import net.muratov.assistant.i18n.tr

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import net.muratov.assistant.ui.ImproverTheme
import net.muratov.assistant.ui.MeetingContextViewModel
import net.muratov.assistant.ui.MarkdownText
import net.muratov.assistant.ui.meetingContextPending
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class MeetingContextActivity : net.muratov.assistant.i18n.LocalizedActivity() {
    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val meetingId = intent.getStringExtra(EXTRA_ID) ?: return finish()
        val application = application as ImproverApplication
        setContent {
            ImproverTheme {
                val model: MeetingContextViewModel = viewModel(factory = MeetingContextViewModel.Factory(
                    application.container.repository, meetingId,
                ))
                val state by model.state.collectAsState()
                val detail = state.detail
                Scaffold(topBar = {
                    TopAppBar(title = { Text(tr("Подготовка к встрече")) }, navigationIcon = {
                        IconButton(onClick = ::finish) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = tr("Назад"))
                        }
                    })
                }) { padding ->
                    Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState())
                        .padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        if (state.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                        state.error?.let {
                            Text(it, color = MaterialTheme.colorScheme.error)
                            Button(onClick = { model.load() }) { Text(tr("Повторить")) }
                        }
                        if (detail != null) {
                            Text(detail.meeting.title, style = MaterialTheme.typography.headlineSmall)
                            Text(formatContextTime(detail.meeting.startsAt))
                            detail.meeting.location?.takeIf { it.isNotBlank() }?.let { Text(it) }
                            val participants = detail.meeting.attendees.mapNotNull {
                                it["name"]?.takeIf(String::isNotBlank) ?: it["address"]
                            }.distinct().joinToString(", ")
                            if (participants.isNotBlank()) Text(participants,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            when (detail.status) {
                                "PENDING", "PROCESSING" -> {
                                    LinearProgressIndicator(Modifier.fillMaxWidth())
                                    Text(if (detail.status == "PENDING") tr("Контекст готовится в фоне…")
                                         else tr("Анализируем предыдущие встречи и переписки…"))
                                }
                                "FAILED" -> Text(detail.error ?: tr("Не удалось подготовить контекст"),
                                    color = MaterialTheme.colorScheme.error)
                                "CANCELLED" -> Text(tr("Встреча отменена"))
                                "ENDED" -> Text(tr("Встреча завершилась"))
                                "NOT_REQUESTED" -> Text(tr("Контекст ещё не запрашивался. Нажмите кнопку, чтобы подготовить его."))
                            }
                            detail.generatedAt?.let { Text(tr("Обновлено: {0}" , formatContextTime(it)),
                                style = MaterialTheme.typography.labelMedium) }
                            detail.summary?.let { summary ->
                                Text(tr("Контекст и договорённости"), style = MaterialTheme.typography.titleMedium)
                                MarkdownText(summary, Modifier.fillMaxWidth())
                            }
                            if (detail.references.isNotEmpty()) {
                                Text(tr("Источники"), style = MaterialTheme.typography.titleMedium)
                                detail.references.forEach { reference ->
                                    Card(onClick = {
                                        val resultId = reference.meetingResultId
                                        startActivity(if (resultId != null)
                                            MeetingResultDetailActivity.intent(this@MeetingContextActivity, resultId)
                                        else EventDetailActivity.intent(this@MeetingContextActivity,
                                            reference.id, reference.sourceLabel))
                                    }, modifier = Modifier.fillMaxWidth()) {
                                        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                            Text("[${reference.key}] ${reference.title}",
                                                style = MaterialTheme.typography.titleSmall)
                                            reference.occurredAt?.let { Text(formatContextTime(it),
                                                style = MaterialTheme.typography.labelSmall) }
                                            Text(reference.snippet, style = MaterialTheme.typography.bodyMedium)
                                        }
                                    }
                                }
                            }
                            TextButton(onClick = { startActivity(EventDetailActivity.intent(
                                this@MeetingContextActivity, detail.meeting.sourceEventId, detail.meeting.sourceLabel,
                            )) }) { Text(tr("Открыть приглашение")) }
                            if (detail.status !in setOf("CANCELLED", "ENDED")) Button(
                                onClick = { model.load(refresh = true) },
                                enabled = !state.loading && !meetingContextPending(detail.status),
                            ) { Text(if (detail.summary == null) tr("Создать контекст") else tr("Обновить контекст")) }
                            if (meetingContextPending(detail.status)) {
                                Text(tr("Можно закрыть экран — по готовности придёт уведомление."),
                                    style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
            }
        }
    }

    companion object {
        private const val EXTRA_ID = "meeting_id"
        fun intent(context: Context, id: String) = Intent(context, MeetingContextActivity::class.java)
            .putExtra(EXTRA_ID, id)
    }
}

private fun formatContextTime(value: String): String = runCatching {
    OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm"))
}.getOrDefault(value)
