package net.muratov.assistant

import android.app.Application
import net.muratov.assistant.data.AppContainer

class ImproverApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        net.muratov.assistant.ui.AdaptiveActivityLayout.install(this, container.settings.isConfigured)
        container.realtime.install()
        container.updates.schedule()
    }
}
