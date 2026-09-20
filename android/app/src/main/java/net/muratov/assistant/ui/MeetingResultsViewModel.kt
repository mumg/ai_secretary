package net.muratov.assistant.ui

import net.muratov.assistant.i18n.tr

import androidx.lifecycle.ViewModel
import net.muratov.assistant.notifications.observeRealtime
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
import net.muratov.assistant.data.remote.MeetingResultDto

data class MeetingResultsUiState(
    val items: List<MeetingResultDto> = emptyList(),
    val loading: Boolean = false,
    val hasMore: Boolean = true,
    val error: String? = null,
    val query: String = "",
    val refreshing: Boolean = false,
)

class MeetingResultsViewModel(private val repository: TaskRepository) : ViewModel() {
    private val mutableState = MutableStateFlow(MeetingResultsUiState())
    val state: StateFlow<MeetingResultsUiState> = mutableState.asStateFlow()
    private var loadJob: Job? = null

    init {
        observeRealtime("results", "events") { refresh() }
        loadNext()
    }

    fun refresh(fromPull: Boolean = false) {
        val current = mutableState.value
        loadJob?.cancel()
        val refreshState = current.copy(
            loading = false,
            hasMore = true,
            refreshing = fromPull || current.refreshing,
            error = null,
        )
        mutableState.value = refreshState
        loadJob = viewModelScope.launch { loadPage(refreshState, replace = true) }
    }

    fun setSearchQuery(value: String) {
        val query = value.take(200)
        if (query == mutableState.value.query) return
        loadJob?.cancel()
        mutableState.value = MeetingResultsUiState(query = query)
        loadJob = viewModelScope.launch {
            delay(300)
            loadPage(mutableState.value)
        }
    }

    fun loadNext() {
        val current = mutableState.value
        if (current.loading || !current.hasMore) return
        loadJob = viewModelScope.launch { loadPage(current) }
    }

    private suspend fun loadPage(current: MeetingResultsUiState, replace: Boolean = false) {
        if (current.loading || !current.hasMore) return
        try {
            mutableState.value = current.copy(loading = true, error = null)
            val target = if (replace) current.items.size.coerceAtLeast(20) else 20
            var offset = if (replace) 0 else current.items.size
            var page = repository.meetingResults(
                offset = offset,
                limit = target.coerceAtMost(100),
                query = current.query.trim().ifBlank { null },
            )
            val received = page.items.toMutableList()
            while (replace && page.hasMore && received.size < target && page.items.isNotEmpty()) {
                offset += page.items.size
                page = repository.meetingResults(
                    offset = offset,
                    limit = (target - received.size).coerceAtMost(100),
                    query = current.query.trim().ifBlank { null },
                )
                received.addAll(page.items)
            }
            mutableState.value = MeetingResultsUiState(
                items = ((if (replace) emptyList() else current.items) + received)
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
                error = exception.message ?: tr("Не удалось загрузить результаты встреч"),
            )
        }
    }

    class Factory(private val repository: TaskRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            MeetingResultsViewModel(repository) as T
    }
}
