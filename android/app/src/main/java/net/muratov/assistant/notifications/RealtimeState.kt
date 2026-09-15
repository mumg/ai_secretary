package net.muratov.assistant.notifications

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Revision counters retain invalidations while a screen/application is stopped. */
object RealtimeState {
    val active = MutableStateFlow(false)
    val connected = MutableStateFlow(false)
    val revisions = MutableStateFlow<Map<String, Long>>(emptyMap())

    fun changed(topics: Collection<String>) {
        revisions.update { previous ->
            previous.toMutableMap().apply {
                topics.filter { it in allowedTopics }.forEach { this[it] = (this[it] ?: 0) + 1 }
            }
        }
    }

    private val allowedTopics = setOf("all", "tasks", "meetings", "contexts", "results",
        "threads", "events", "chat", "status")
}

fun ViewModel.observeRealtime(vararg topics: String, refresh: () -> Unit) {
    viewModelScope.launch {
        RealtimeState.active.collectLatest { active ->
            if (active) {
                RealtimeState.revisions
                    .map { versions -> (topics.toSet() + "all").sumOf { versions[it] ?: 0 } }
                    .distinctUntilChanged()
                    .conflate()
                    .collect {
                        delay(300)
                        refresh()
                    }
            }
        }
    }
}
