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
import net.muratov.assistant.data.remote.EventDto

data class EventDetailUiState(
    val loading: Boolean = true,
    val event: EventDto? = null,
    val error: String? = null,
)

class EventDetailViewModel(
    private val repository: TaskRepository,
    private val eventId: String,
) : ViewModel() {
    private val _state = MutableStateFlow(EventDetailUiState())
    val state: StateFlow<EventDetailUiState> = _state.asStateFlow()

    private var loadJob: Job? = null

    init {
        observeRealtime("events") { load(silent = true) }
        load()
    }

    fun load(silent: Boolean = false) {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            if (!silent) _state.value = _state.value.copy(loading = true, error = null)
            runCatching { repository.event(eventId) }
                .onSuccess { _state.value = EventDetailUiState(loading = false, event = it) }
                .onFailure {
                    if (it is CancellationException) throw it
                    _state.value = _state.value.copy(
                        loading = false,
                        error = it.message ?: tr("Не удалось загрузить источник"),
                    )
                }
        }
    }

    class Factory(
        private val repository: TaskRepository,
        private val eventId: String,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            EventDetailViewModel(repository, eventId) as T
    }
}
