package net.muratov.assistant.ui

import net.muratov.assistant.i18n.tr

import androidx.lifecycle.ViewModel
import net.muratov.assistant.notifications.observeRealtime
import kotlinx.coroutines.Job
import kotlinx.coroutines.CancellationException
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

    private var loadJob: Job? = null

    init {
        observeRealtime("threads", "events") { load(silent = true) }
        load()
    }

    fun load(silent: Boolean = false) {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            if (!silent) mutableState.value = mutableState.value.copy(loading = true, error = null)
            mutableState.value = try {
                ConversationThreadDetailUiState(
                    detail = repository.thread(threadId),
                    loading = false,
                )
            } catch (exception: Exception) {
                if (exception is CancellationException) throw exception
                mutableState.value.copy(
                    loading = false,
                    error = exception.message ?: tr("Не удалось загрузить переписку"),
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
