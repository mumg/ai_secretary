package net.muratov.assistant

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import net.muratov.assistant.data.remote.ChatReferenceDto
import net.muratov.assistant.notifications.ChatNotificationState
import net.muratov.assistant.ui.ChatMessageUi
import net.muratov.assistant.ui.ChatViewModel
import net.muratov.assistant.ui.ImproverTheme
import net.muratov.assistant.ui.MarkdownText
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class ChatActivity : ComponentActivity() {
    override fun onStart() {
        super.onStart()
        ChatNotificationState.visible = true
    }

    override fun onStop() {
        ChatNotificationState.visible = false
        super.onStop()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val application = application as ImproverApplication
        setContent {
            ImproverTheme {
                val chatViewModel: ChatViewModel = viewModel(
                    factory = ChatViewModel.Factory(application.container.repository),
                )
                val state by chatViewModel.state.collectAsState()
                ChatScreen(
                    messages = state.messages,
                    loading = state.loading,
                    error = state.error,
                    onBack = ::finish,
                    onSend = chatViewModel::send,
                    onOpenTask = { startActivity(TaskDetailActivity.intent(this, it)) },
                    onOpenEvent = { id, label ->
                        startActivity(EventDetailActivity.intent(this, id, label))
                    },
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatScreen(
    messages: List<ChatMessageUi>,
    loading: Boolean,
    error: String?,
    onBack: () -> Unit,
    onSend: (String) -> Unit,
    onOpenTask: (String) -> Unit,
    onOpenEvent: (String, String?) -> Unit,
) {
    var input by rememberSaveable { mutableStateOf("") }
    val listState = rememberLazyListState()
    LaunchedEffect(messages.size, messages.lastOrNull()?.content, loading) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.lastIndex)
    }
    val submit = {
        if (input.isNotBlank()) {
            onSend(input)
            input = ""
        }
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Чат с Qwen") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Назад")
                    }
                },
            )
        },
        bottomBar = {
            Row(
                Modifier.fillMaxWidth().imePadding().padding(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = input,
                    onValueChange = { input = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("Вопрос по задачам и переписке") },
                    maxLines = 5,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { submit() }),
                )
                IconButton(onClick = submit, enabled = input.isNotBlank()) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Отправить")
                }
            }
        },
    ) { padding ->
        if (loading && messages.isEmpty()) {
            Column(
                Modifier.fillMaxSize().padding(padding),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                CircularProgressIndicator()
            }
        } else if (messages.isEmpty()) {
            Column(
                Modifier.fillMaxSize().padding(padding).padding(32.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("Спросите о задачах, письмах, сообщениях или встречах")
                Text(
                    error ?: "Qwen найдёт подходящие записи в сохранённом архиве и покажет источники.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 12.dp),
                state = listState,
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                itemsIndexed(messages) { _, message ->
                    ChatBubble(message, onOpenTask, onOpenEvent)
                }
            }
        }
    }
}

@Composable
private fun ChatBubble(
    message: ChatMessageUi,
    onOpenTask: (String) -> Unit,
    onOpenEvent: (String, String?) -> Unit,
) {
    val user = message.role == "user"
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (user) Arrangement.End else Arrangement.Start,
    ) {
        Surface(
            modifier = Modifier.widthIn(max = 560.dp),
            shape = RoundedCornerShape(18.dp),
            color = when {
                message.failed -> MaterialTheme.colorScheme.errorContainer
                user -> MaterialTheme.colorScheme.primaryContainer
                else -> MaterialTheme.colorScheme.surfaceVariant
            },
        ) {
            Column(Modifier.padding(14.dp)) {
                if (message.pending) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                        )
                        Text(message.content, modifier = Modifier.padding(start = 8.dp))
                    }
                } else if (message.content.isNotBlank()) {
                    if (user || message.failed) {
                        Text(message.content)
                    } else {
                        MarkdownText(message.content, Modifier.fillMaxWidth())
                    }
                }
                if (message.references.isNotEmpty()) {
                    Text(
                        "Источники",
                        style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.padding(top = 12.dp, bottom = 4.dp),
                    )
                    message.references.forEach { reference ->
                        ReferenceCard(reference, onOpenTask, onOpenEvent)
                    }
                }
            }
        }
    }
}

@Composable
private fun ReferenceCard(
    reference: ChatReferenceDto,
    onOpenTask: (String) -> Unit,
    onOpenEvent: (String, String?) -> Unit,
) {
    val uriHandler = LocalUriHandler.current
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        onClick = {
            when {
                reference.kind == "task" -> onOpenTask(reference.id)
                else -> onOpenEvent(reference.id, reference.sourceLabel)
            }
        },
    ) {
        Column(Modifier.padding(10.dp)) {
            Text(
                "[${reference.key}] ${reference.title}",
                style = MaterialTheme.typography.titleSmall,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            val metadata = listOfNotNull(reference.sourceLabel, formatReferenceDate(reference.occurredAt))
                .joinToString(" · ")
            if (metadata.isNotBlank()) {
                Text(metadata, style = MaterialTheme.typography.labelSmall)
            }
            if (reference.snippet.isNotBlank()) {
                Text(
                    reference.snippet,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 4,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
            if (reference.kind == "event" && !reference.sourceUrl.isNullOrBlank()) {
                TextButton(onClick = { uriHandler.openUri(reference.sourceUrl) }) {
                    Text("Открыть в источнике")
                }
            }
        }
    }
}

private fun formatReferenceDate(value: String?): String? = value?.let {
    runCatching {
        OffsetDateTime.parse(it).atZoneSameInstant(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm"))
    }.getOrDefault(it)
}
