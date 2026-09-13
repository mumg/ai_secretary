package net.muratov.assistant

import android.app.Application
import net.muratov.assistant.data.AppContainer

class ImproverApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}

