package net.muratov.assistant

import android.Manifest
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.espresso.Espresso
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import net.muratov.assistant.data.SettingsStore
import net.muratov.assistant.setup.IdentityCaptureActivity
import net.muratov.assistant.setup.SetupWizard
import net.muratov.assistant.ui.ImproverTheme
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class IdentityScannerTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Test fun opensPrivateQRScannerAndReturnsWithoutChangingConnection() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val settings = SettingsStore(context)
        val beforeUrl = settings.serverUrl
        val beforeAlias = settings.certificateAlias
        instrumentation.uiAutomation.grantRuntimePermission(context.packageName, Manifest.permission.CAMERA)
        compose.setContent { ImproverTheme { SetupWizard(settings, onComplete = {}) } }
        compose.onNodeWithText("Сканировать QR-код").performClick()
        compose.waitUntil(10_000) {
            var found = false
            instrumentation.runOnMainSync {
                found = ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
                    .any { it is IdentityCaptureActivity && it.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0 }
            }
            found
        }
        Espresso.pressBackUnconditionally()
        compose.waitForIdle()
        assertEquals(beforeUrl, settings.serverUrl)
        assertEquals(beforeAlias, settings.certificateAlias)
    }
}
