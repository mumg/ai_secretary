package net.muratov.assistant.data

import android.content.Context
import net.muratov.assistant.data.local.AppDatabase

class AppContainer(context: Context) {
    val settings = SettingsStore(context)
    private val database = AppDatabase.create(context)
    val repository = TaskRepository(context, database.taskDao(), settings)
}

