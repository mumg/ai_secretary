package net.muratov.assistant.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import net.muratov.assistant.data.TaskRepository
import net.muratov.assistant.data.remote.ConversationThreadDto

data class ThreadsUiState(
    val items: List<ConversationThreadDto> = emptyList(),
    val loading: Boolean = false,
    val hasMore: Boolean = true,
    val error: String? = null,
    val query: String = "",
)

class ThreadsViewModel(private val repository: TaskRepository) : ViewModel() {
    private val mutableState = MutableStateFlow(ThreadsUiState())
    val state: StateFlow<ThreadsUiState> = mutableState.asStateFlow()
    private var loadJob: Job? = null

    init {
        loadNext()
    }

    fun refresh() {
        val query = mutableState.value.query
        loadJob?.cancel()
        mutableState.value = ThreadsUiState(query = query)
        loadNext()
    }

    fun setSearchQuery(value: String) {
        val query = value.take(200)
        if (query == mutableState.value.query) return
        loadJob?.cancel()
        mutableState.value = ThreadsUiState(query = query)
        loadJob = viewModelScope.launch {
            delay(300)
            loadPage(mutableState.value)
        }
    }

    fun loadNext() {
        val current = mutableState.value
        if (current.loading || !current.hasMore) return
        loadJob = viewModelScope.launch {
            loadPage(current)
        }
    }

    private suspend fun loadPage(current: ThreadsUiState) {
        if (current.loading || !current.hasMore) return
        try {
            mutableState.value = current.copy(loading = true, error = null)
            val page = repository.threads(
                offset = current.items.size,
                query = current.query.trim().ifBlank { null },
            )
            mutableState.value = ThreadsUiState(
                items = (current.items + page.items).distinctBy { it.id },
                loading = false,
                hasMore = page.hasMore,
                query = current.query,
            )
        } catch (exception: CancellationException) {
            throw exception
        } catch (exception: Exception) {
            mutableState.value = current.copy(
                loading = false,
                error = exception.message ?: "Не удалось загрузить переписки",
            )
        }
    }

    class Factory(private val repository: TaskRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ThreadsViewModel(repository) as T
    }
}
