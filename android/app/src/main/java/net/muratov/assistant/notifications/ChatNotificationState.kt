package net.muratov.assistant.notifications

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow

object ChatNotificationState {
    @Volatile
    var visible: Boolean = false

    private val updates = MutableSharedFlow<String>(extraBufferCapacity = 16)
    val events = updates.asSharedFlow()

    fun notifyChanged(requestId: String) {
        updates.tryEmit(requestId)
    }
}
