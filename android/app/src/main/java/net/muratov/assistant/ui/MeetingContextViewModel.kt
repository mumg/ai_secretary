package net.muratov.assistant.ui

import net.muratov.assistant.i18n.tr

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
import net.muratov.assistant.data.remote.MeetingContextDto
import net.muratov.assistant.notifications.observeRealtime
import net.muratov.assistant.notifications.RealtimeState

data class MeetingContextUiState(
    val detail: MeetingContextDto? = null,
    val loading: Boolean = true,
    val error: String? = null,
)

fun meetingContextPending(status: String): Boolean = status in setOf("PENDING", "PROCESSING")

class MeetingContextViewModel(
    private val repository: TaskRepository,
    private val meetingId: String,
) : ViewModel() {
    private val mutableState = MutableStateFlow(MeetingContextUiState())
    val state: StateFlow<MeetingContextUiState> = mutableState.asStateFlow()
    private var job: Job? = null

    init {
        observeRealtime("contexts", "meetings", "events") { load(silent = true) }
        load()
    }

    fun load(refresh: Boolean = false, silent: Boolean = false) {
        job?.cancel()
        job = viewModelScope.launch {
            if (!silent) mutableState.value = mutableState.value.copy(loading = true, error = null)
            try {
                var detail = if (refresh) repository.refreshMeetingContext(meetingId)
                             else repository.meetingContext(meetingId)
                mutableState.value = MeetingContextUiState(detail = detail, loading = false)
                while (meetingContextPending(detail.status)) {
                    delay(3_000)
                    if (!RealtimeState.active.value || RealtimeState.connected.value) break
                    detail = repository.meetingContext(meetingId)
                    mutableState.value = MeetingContextUiState(detail = detail, loading = false)
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                mutableState.value = mutableState.value.copy(
                    loading = false, error = error.message ?: tr("Не удалось загрузить контекст встречи"),
                )
            }
        }
    }

    class Factory(private val repository: TaskRepository, private val meetingId: String) :
        ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            MeetingContextViewModel(repository, meetingId) as T
    }
}
