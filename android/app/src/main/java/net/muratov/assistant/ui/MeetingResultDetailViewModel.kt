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
import net.muratov.assistant.data.remote.MeetingResultDetailDto

data class MeetingResultDetailUiState(
    val detail: MeetingResultDetailDto? = null,
    val loading: Boolean = true,
    val error: String? = null,
)

class MeetingResultDetailViewModel(
    private val repository: TaskRepository,
    private val resultId: String,
) : ViewModel() {
    private val mutableState = MutableStateFlow(MeetingResultDetailUiState())
    val state: StateFlow<MeetingResultDetailUiState> = mutableState.asStateFlow()

    private var loadJob: Job? = null

    init {
        observeRealtime("results", "events") { load(silent = true) }
        load()
    }

    fun load(silent: Boolean = false) {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            if (!silent) mutableState.value = mutableState.value.copy(loading = true, error = null)
            mutableState.value = try {
                MeetingResultDetailUiState(
                    detail = repository.meetingResult(resultId),
                    loading = false,
                )
            } catch (exception: Exception) {
                if (exception is CancellationException) throw exception
                mutableState.value.copy(
                    loading = false,
                    error = exception.message ?: "Не удалось загрузить результат встречи",
                )
            }
        }
    }

    class Factory(
        private val repository: TaskRepository,
        private val resultId: String,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            MeetingResultDetailViewModel(repository, resultId) as T
    }
}
