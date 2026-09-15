package net.muratov.assistant.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import net.muratov.assistant.data.TaskRepository
import net.muratov.assistant.data.remote.SystemStatusDto

data class SystemStatusUiState(
    val snapshot: SystemStatusDto? = null,
    val loading: Boolean = false,
    val error: String? = null,
    val refreshing: Boolean = false,
)

class SystemStatusViewModel(private val repository: TaskRepository) : ViewModel() {
    private val mutableState = MutableStateFlow(SystemStatusUiState())
    val state: StateFlow<SystemStatusUiState> = mutableState.asStateFlow()
    private var requestInProgress = false

    init {
        viewModelScope.launch {
            while (isActive) {
                load()
                delay(15_000)
            }
        }
    }

    fun refresh() {
        if (requestInProgress) return
        mutableState.value = mutableState.value.copy(refreshing = true)
        viewModelScope.launch { load() }
    }

    private suspend fun load() {
        if (requestInProgress) return
        requestInProgress = true
        mutableState.value = mutableState.value.copy(loading = true, error = null)
        try {
            mutableState.value = SystemStatusUiState(snapshot = repository.systemStatus())
        } catch (exception: CancellationException) {
            throw exception
        } catch (exception: Exception) {
            mutableState.value = mutableState.value.copy(
                loading = false,
                refreshing = false,
                error = exception.message ?: "Не удалось получить состояние системы",
            )
        } finally {
            requestInProgress = false
            mutableState.value = mutableState.value.copy(loading = false, refreshing = false)
        }
    }

    class Factory(private val repository: TaskRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            SystemStatusViewModel(repository) as T
    }
}
