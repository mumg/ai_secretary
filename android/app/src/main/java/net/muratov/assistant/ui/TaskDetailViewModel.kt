package net.muratov.assistant.ui

import androidx.lifecycle.ViewModel
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

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            _state.value = TaskDetailUiState(loading = true)
            runCatching { repository.detail(taskId) }
                .onSuccess { _state.value = TaskDetailUiState(loading = false, detail = it) }
                .onFailure {
                    _state.value = TaskDetailUiState(
                        loading = false,
                        error = it.message ?: "Не удалось загрузить задачу",
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
