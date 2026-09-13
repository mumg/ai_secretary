package net.muratov.assistant.ui

import androidx.lifecycle.ViewModel
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

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            _state.value = EventDetailUiState(loading = true)
            runCatching { repository.event(eventId) }
                .onSuccess { _state.value = EventDetailUiState(loading = false, event = it) }
                .onFailure {
                    _state.value = EventDetailUiState(
                        loading = false,
                        error = it.message ?: "Не удалось загрузить источник",
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
