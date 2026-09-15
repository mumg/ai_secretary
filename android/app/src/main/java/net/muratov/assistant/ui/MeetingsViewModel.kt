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
import net.muratov.assistant.data.remote.MeetingDto

data class MeetingsUiState(
    val items: List<MeetingDto> = emptyList(),
    val loading: Boolean = false,
    val hasMore: Boolean = true,
    val error: String? = null,
    val query: String = "",
    val refreshing: Boolean = false,
)

class MeetingsViewModel(private val repository: TaskRepository) : ViewModel() {
    private val mutableState = MutableStateFlow(MeetingsUiState())
    val state: StateFlow<MeetingsUiState> = mutableState.asStateFlow()
    private var loadJob: Job? = null

    init {
        loadNext()
    }

    fun refresh() {
        val current = mutableState.value
        loadJob?.cancel()
        val refreshState = current.copy(
            loading = false,
            hasMore = true,
            refreshing = true,
            error = null,
        )
        mutableState.value = refreshState
        loadJob = viewModelScope.launch { loadPage(refreshState, replace = true) }
    }

    fun setSearchQuery(value: String) {
        val query = value.take(200)
        if (query == mutableState.value.query) return
        loadJob?.cancel()
        mutableState.value = MeetingsUiState(query = query)
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

    private suspend fun loadPage(current: MeetingsUiState, replace: Boolean = false) {
        if (current.loading || !current.hasMore) return
        try {
            mutableState.value = current.copy(loading = true, error = null)
            val page = repository.meetings(
                offset = if (replace) 0 else current.items.size,
                query = current.query.trim().ifBlank { null },
            )
            mutableState.value = MeetingsUiState(
                items = ((if (replace) emptyList() else current.items) + page.items)
                    .distinctBy { it.id },
                loading = false,
                hasMore = page.hasMore,
                query = current.query,
                refreshing = false,
            )
        } catch (exception: CancellationException) {
            throw exception
        } catch (exception: Exception) {
            mutableState.value = current.copy(
                loading = false,
                refreshing = false,
                error = exception.message ?: "Не удалось загрузить встречи",
            )
        }
    }

    class Factory(private val repository: TaskRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            MeetingsViewModel(repository) as T
    }
}
