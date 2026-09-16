package net.muratov.assistant.ui

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
import net.muratov.assistant.data.remote.TaskDetailDto

data class TaskDetailUiState(
    val loading: Boolean = true,
    val detail: TaskDetailDto? = null,
    val error: String? = null,
)

class TaskDetailViewModel(
    private val repository: TaskRepository,
    private val taskId: String,
) : ViewModel() {
    private val _state = MutableStateFlow(TaskDetailUiState())
    val state: StateFlow<TaskDetailUiState> = _state.asStateFlow()

    private var loadJob: Job? = null
    private var rejectJob: Job? = null

    init {
        observeRealtime("tasks", "events") { load(silent = true) }
        load()
    }

    fun load(silent: Boolean = false) {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            if (!silent) _state.value = _state.value.copy(loading = true, error = null)
            runCatching { repository.detail(taskId) }
                .onSuccess { _state.value = TaskDetailUiState(loading = false, detail = it) }
                .onFailure {
                    if (it is CancellationException) throw it
                    _state.value = _state.value.copy(
                        loading = false,
                        error = it.message ?: "Не удалось загрузить задачу",
                    )
                }
        }
    }

    fun reject() {
        if (rejectJob?.isActive == true) return
        loadJob?.cancel()
        rejectJob = viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                repository.reject(taskId)
                load()
            } catch (exception: CancellationException) {
                throw exception
            } catch (exception: Exception) {
                _state.value = _state.value.copy(
                    loading = false,
                    error = exception.message ?: "Не удалось отказаться от задачи",
                )
            }
        }
    }

    class Factory(
        private val repository: TaskRepository,
        private val taskId: String,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            TaskDetailViewModel(repository, taskId) as T
    }
}
