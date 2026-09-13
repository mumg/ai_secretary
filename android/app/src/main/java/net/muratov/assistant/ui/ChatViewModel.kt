package net.muratov.assistant.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import net.muratov.assistant.data.TaskRepository
import net.muratov.assistant.data.remote.ChatHistoryMessageDto
import net.muratov.assistant.data.remote.ChatReferenceDto

data class ChatMessageUi(
    val role: String,
    val content: String,
    val references: List<ChatReferenceDto> = emptyList(),
    val failed: Boolean = false,
)

data class ChatUiState(
    val messages: List<ChatMessageUi> = emptyList(),
    val loading: Boolean = false,
    val progress: String? = null,
)

class ChatViewModel(private val repository: TaskRepository) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    fun send(text: String) {
        val query = text.trim()
        if (query.isEmpty() || _state.value.loading) return
        val history = _state.value.messages
            .filterNot(ChatMessageUi::failed)
            .takeLast(12)
            .map { ChatHistoryMessageDto(role = it.role, content = it.content) }
        _state.value = _state.value.copy(
            messages = _state.value.messages + ChatMessageUi("user", query),
            loading = true,
            progress = "Ищу в смысловом индексе…",
        )
        viewModelScope.launch {
            try {
                repository.chatStream(query, history).collect { event ->
                    when (event.type) {
                        "status" -> _state.value = _state.value.copy(progress = event.message)
                        "references" -> {
                            mergeReferences(event.references.orEmpty())
                            _state.value = _state.value.copy(
                                progress = event.message ?: "Qwen формирует ответ…",
                            )
                        }
                        "answer_delta" -> appendAnswer(
                            event.delta.orEmpty(),
                            event.references.orEmpty(),
                        )
                        "complete" -> {
                            mergeReferences(event.references.orEmpty())
                            _state.value = _state.value.copy(loading = false, progress = null)
                        }
                        "error" -> error(event.message ?: "Не удалось получить ответ от Qwen")
                    }
                }
                if (_state.value.loading) {
                    _state.value = _state.value.copy(loading = false, progress = null)
                }
            } catch (error: Exception) {
                val messages = _state.value.messages
                val failure = ChatMessageUi(
                    role = "assistant",
                    content = error.message ?: "Не удалось получить ответ от Qwen",
                    failed = true,
                )
                _state.value = _state.value.copy(
                    messages = if (messages.lastOrNull()?.role == "assistant") {
                        messages.dropLast(1) + failure
                    } else {
                        messages + failure
                    },
                    loading = false,
                    progress = null,
                )
            }
        }
    }

    private fun appendAnswer(delta: String, references: List<ChatReferenceDto>) {
        val messages = _state.value.messages
        val last = messages.lastOrNull()
        val updated = if (last?.role == "assistant") {
            messages.dropLast(1) + last.copy(
                content = last.content + delta,
                references = (last.references + references).distinctBy(ChatReferenceDto::key),
            )
        } else {
            messages + ChatMessageUi("assistant", delta, references)
        }
        _state.value = _state.value.copy(
            messages = updated,
            progress = "Qwen формирует ответ…",
        )
    }

    private fun mergeReferences(references: List<ChatReferenceDto>) {
        if (references.isEmpty()) return
        val messages = _state.value.messages
        val last = messages.lastOrNull()
        val updated = if (last?.role == "assistant") {
            messages.dropLast(1) + last.copy(
                references = (last.references + references).distinctBy(ChatReferenceDto::key),
            )
        } else {
            messages + ChatMessageUi("assistant", "", references)
        }
        _state.value = _state.value.copy(messages = updated)
    }

    class Factory(private val repository: TaskRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ChatViewModel(repository) as T
    }
}
