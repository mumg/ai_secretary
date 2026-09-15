package net.muratov.assistant.ui

import androidx.lifecycle.ViewModel
import net.muratov.assistant.notifications.observeRealtime
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import net.muratov.assistant.data.TaskRepository
import net.muratov.assistant.data.local.TaskEntity
import net.muratov.assistant.data.remote.MeetingDto

class TaskViewModel(private val repository: TaskRepository) : ViewModel() {
    val tasks: StateFlow<List<TaskEntity>> = repository.tasks.stateIn(
        scope = viewModelScope,
        started = SharingStarted.WhileSubscribed(5_000),
        initialValue = emptyList(),
    )
    val loading = MutableStateFlow(false)
    val refreshing = MutableStateFlow(false)
    val voiceProcessing = MutableStateFlow(false)
    val error = MutableStateFlow<String?>(null)
    val todayMeetings = MutableStateFlow<List<MeetingDto>>(emptyList())
    val searchQuery = MutableStateFlow("")
    val searchResults = MutableStateFlow<List<TaskEntity>?>(null)
    val searchLoading = MutableStateFlow(false)
    val searchError = MutableStateFlow<String?>(null)
    private var searchJob: Job? = null

    init {
        observeRealtime("tasks", "meetings") { refresh() }
        refresh()
    }

    fun refresh() = execute {
        repository.refresh()
        todayMeetings.value = repository.today().meetings
        updateSearch()
    }

    fun refreshFromPull() {
        refreshing.value = true
        execute(onFinished = { refreshing.value = false }) {
            repository.refresh()
            todayMeetings.value = repository.today(refresh = true).meetings
            updateSearch()
        }
    }

    fun setSearchQuery(value: String) {
        val limitedValue = value.take(200)
        searchQuery.value = limitedValue
        searchJob?.cancel()
        searchError.value = null
        if (limitedValue.isBlank()) {
            searchResults.value = null
            searchLoading.value = false
            return
        }
        searchResults.value = emptyList()
        searchLoading.value = true
        searchJob = viewModelScope.launch {
            delay(300)
            updateSearch(limitedValue.trim())
        }
    }

    fun create(title: String, priority: String, dueAt: String?) = execute {
        repository.create(title, priority, dueAt?.ifBlank { null })
        updateSearch()
    }

    fun createFromVoice(text: String) {
        viewModelScope.launch {
            voiceProcessing.value = true
            error.value = null
            try {
                repository.createFromText(text)
                updateSearch()
            } catch (exception: Exception) {
                error.value = exception.message ?: "Не удалось оформить голосовую задачу"
            } finally {
                voiceProcessing.value = false
            }
        }
    }

    fun complete(id: String) = execute {
        repository.complete(id)
        updateSearch()
    }
    fun confirm(id: String) = execute {
        repository.confirm(id)
        updateSearch()
    }
    fun reject(id: String) = execute {
        repository.reject(id)
        updateSearch()
    }
    fun addReminder(id: String, remindAt: String) = execute {
        repository.addReminder(id, remindAt)
    }
    fun registerDevice() = execute { repository.registerFcmToken() }

    private suspend fun updateSearch(query: String = searchQuery.value.trim()) {
        if (query.isBlank()) {
            searchResults.value = null
            return
        }
        searchLoading.value = true
        searchError.value = null
        try {
            searchResults.value = repository.searchTasks(query)
        } catch (exception: CancellationException) {
            throw exception
        } catch (exception: Exception) {
            searchError.value = exception.message ?: "Не удалось выполнить поиск задач"
        } finally {
            searchLoading.value = false
        }
    }

    private fun execute(onFinished: () -> Unit = {}, block: suspend () -> Unit) {
        viewModelScope.launch {
            loading.value = true
            error.value = null
            runCatching { block() }.onFailure { error.value = it.message ?: "Ошибка соединения" }
            loading.value = false
            onFinished()
        }
    }

    class Factory(private val repository: TaskRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            TaskViewModel(repository) as T
    }
}
