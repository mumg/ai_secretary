package net.muratov.assistant.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import net.muratov.assistant.data.TaskRepository
import net.muratov.assistant.data.remote.ChatHistoryMessageDto
import net.muratov.assistant.data.remote.ChatReferenceDto
import net.muratov.assistant.data.remote.ChatRequestDto
import net.muratov.assistant.notifications.ChatNotificationState
import net.muratov.assistant.notifications.RealtimeState
import net.muratov.assistant.notifications.observeRealtime
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

data class ChatMessageUi(
    val role: String,
    val content: String,
    val references: List<ChatReferenceDto> = emptyList(),
    val failed: Boolean = false,
    val pending: Boolean = false,
    val requestId: String? = null,
)

data class ChatUiState(
    val messages: List<ChatMessageUi> = emptyList(),
    val loading: Boolean = true,
    val error: String? = null,
)

class ChatViewModel(private val repository: TaskRepository) : ViewModel() {
    private data class LocalRequest(val query: String, val error: String? = null)

    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()
    private var requests: List<ChatRequestDto> = emptyList()
    private val localRequests = linkedMapOf<String, LocalRequest>()
    private val refreshMutex = Mutex()

    init {
        observeRealtime("chat") { viewModelScope.launch { refresh(silent = true) } }
        viewModelScope.launch {
            refresh()
            while (true) {
                val hasPending = requests.any {
                    it.status == "PENDING" || it.status == "PROCESSING"
                }
                delay(if (hasPending) 2_000 else 15_000)
                if (RealtimeState.active.value && !RealtimeState.connected.value) refresh(silent = true)
            }
        }
        viewModelScope.launch {
            ChatNotificationState.events.collect { refresh(silent = true) }
        }
    }

    fun send(text: String) {
        val query = text.trim()
        if (query.isEmpty()) return
        val history = requests
            .filter { it.status == "COMPLETED" && !it.answer.isNullOrBlank() }
            .takeLast(6)
            .flatMap {
                listOf(
                    ChatHistoryMessageDto("user", it.query),
                    ChatHistoryMessageDto("assistant", it.answer.orEmpty()),
                )
        }
        val temporaryId = "local-${System.nanoTime()}"
        localRequests[temporaryId] = LocalRequest(query)
        _state.value = _state.value.copy(
            messages = renderedMessages(),
            error = null,
        )
        viewModelScope.launch {
            try {
                repository.enqueueChat(query, history)
                localRequests.remove(temporaryId)
                refresh(silent = true)
            } catch (error: Exception) {
                localRequests[temporaryId] = LocalRequest(
                    query,
                    error.message ?: "Не удалось отправить вопрос",
                )
                _state.value = _state.value.copy(messages = renderedMessages())
            }
        }
    }

    private suspend fun refresh(silent: Boolean = false) = refreshMutex.withLock {
        try {
            val remote = repository.chatRequests()
            requests = remote
            _state.value = ChatUiState(
                messages = renderedMessages(),
                loading = false,
            )
        } catch (error: Exception) {
            if (!silent || _state.value.messages.isEmpty()) {
                _state.value = _state.value.copy(
                    loading = false,
                    error = error.message ?: "Не удалось загрузить чат",
                )
            }
        }
    }

    private fun renderedMessages(): List<ChatMessageUi> =
        requests.flatMap(::requestMessages) + localRequests.flatMap { (id, request) ->
            listOf(
                ChatMessageUi("user", request.query, requestId = id),
                ChatMessageUi(
                    role = "assistant",
                    content = request.error ?: "В очереди…",
                    failed = request.error != null,
                    pending = request.error == null,
                    requestId = id,
                ),
            )
        }

    private fun requestMessages(request: ChatRequestDto): List<ChatMessageUi> {
        val response = when (request.status) {
            "COMPLETED" -> ChatMessageUi(
                role = "assistant",
                content = request.answer.orEmpty(),
                references = request.references,
                requestId = request.id,
            )
            "PROCESSING" -> ChatMessageUi(
                role = "assistant",
                content = "Qwen анализирует архив…",
                pending = true,
                requestId = request.id,
            )
            "FAILED" -> ChatMessageUi(
                role = "assistant",
                content = request.error ?: "Не удалось получить ответ",
                failed = true,
                requestId = request.id,
            )
            else -> ChatMessageUi(
                role = "assistant",
                content = if (request.error.isNullOrBlank()) {
                    "В очереди…"
                } else {
                    "Ollama временно недоступна, запрос будет повторён автоматически…"
                },
                pending = true,
                requestId = request.id,
            )
        }
        return listOf(
            ChatMessageUi("user", request.query, requestId = request.id),
            response,
        )
    }

    class Factory(private val repository: TaskRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ChatViewModel(repository) as T
    }
}
