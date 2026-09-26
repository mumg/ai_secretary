package net.muratov.assistant

import android.app.Application
import net.muratov.assistant.data.AppContainer

class ImproverApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        net.muratov.assistant.i18n.Language.initialize(this)
        net.muratov.assistant.ui.OrientationPolicy.install(this)
        container = AppContainer(this)
        net.muratov.assistant.ui.AdaptiveActivityLayout.install(this, container.settings.isConfigured)
        container.realtime.install()
        androidx.work.WorkManager.getInstance(this).cancelUniqueWork("android-updates")
    }
}
