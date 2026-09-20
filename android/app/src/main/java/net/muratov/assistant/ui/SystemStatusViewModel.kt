package net.muratov.assistant.ui

import net.muratov.assistant.i18n.tr

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
import net.muratov.assistant.notifications.RealtimeState
import net.muratov.assistant.notifications.observeRealtime

data class SystemStatusUiState(
    val snapshot: SystemStatusDto? = null,
    val loading: Boolean = false,
    val error: String? = null,
    val refreshing: Boolean = false,
    val serverReachable: Boolean? = null,
)

class SystemStatusViewModel(private val repository: TaskRepository) : ViewModel() {
    private val mutableState = MutableStateFlow(SystemStatusUiState())
    val state: StateFlow<SystemStatusUiState> = mutableState.asStateFlow()
    private var requestInProgress = false
    private var consecutiveFailures = 0

    init {
        observeRealtime("status", "tasks", "events", "chat", "contexts") { refresh() }
        viewModelScope.launch {
            while (isActive) {
                if (RealtimeState.active.value && !RealtimeState.connected.value) load()
                delay(15_000)
            }
        }
    }

    fun refresh(fromPull: Boolean = false) {
        if (fromPull) mutableState.value = mutableState.value.copy(refreshing = true)
        if (requestInProgress) return
        viewModelScope.launch { load() }
    }

    private suspend fun load() {
        if (requestInProgress) return
        requestInProgress = true
        mutableState.value = mutableState.value.copy(loading = true, error = null)
        try {
            val snapshot = repository.systemStatus()
            consecutiveFailures = 0
            mutableState.value = SystemStatusUiState(snapshot = snapshot, serverReachable = true)
        } catch (exception: CancellationException) {
            throw exception
        } catch (exception: Exception) {
            consecutiveFailures++
            mutableState.value = mutableState.value.copy(
                loading = false,
                refreshing = false,
                error = exception.message ?: tr("Не удалось получить состояние системы"),
                // A reconnect while resuming is not yet a confirmed outage.
                serverReachable = if (consecutiveFailures >= 2) false else null,
            )
            if (consecutiveFailures == 1) viewModelScope.launch {
                delay(3_000)
                if (RealtimeState.active.value) load()
            }
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
