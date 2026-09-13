package net.muratov.assistant.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import net.muratov.assistant.data.TaskRepository
import net.muratov.assistant.data.remote.ConversationThreadDetailDto

data class ConversationThreadDetailUiState(
    val detail: ConversationThreadDetailDto? = null,
    val loading: Boolean = true,
    val error: String? = null,
)

class ConversationThreadDetailViewModel(
    private val repository: TaskRepository,
    private val threadId: String,
) : ViewModel() {
    private val mutableState = MutableStateFlow(ConversationThreadDetailUiState())
    val state: StateFlow<ConversationThreadDetailUiState> = mutableState.asStateFlow()

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            mutableState.value = ConversationThreadDetailUiState(loading = true)
            mutableState.value = try {
                ConversationThreadDetailUiState(
                    detail = repository.thread(threadId),
                    loading = false,
                )
            } catch (exception: Exception) {
                ConversationThreadDetailUiState(
                    loading = false,
                    error = exception.message ?: "Не удалось загрузить переписку",
                )
            }
        }
    }

    class Factory(
        private val repository: TaskRepository,
        private val threadId: String,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ConversationThreadDetailViewModel(repository, threadId) as T
    }
}
