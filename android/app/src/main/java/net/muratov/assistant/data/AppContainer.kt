package net.muratov.assistant.data

import android.content.Context
import net.muratov.assistant.data.local.AppDatabase
import net.muratov.assistant.notifications.RealtimeClient
import android.app.Application

class AppContainer(context: Context) {
    val settings = SettingsStore(context)
    val updates = net.muratov.assistant.updates.AppUpdates(context.applicationContext)
    private val database = AppDatabase.create(context)
    val repository = TaskRepository(context, database.taskDao(), settings)
    val realtime = RealtimeClient(context.applicationContext as Application, settings) {
        repository.registerFcmToken()
    }
}
