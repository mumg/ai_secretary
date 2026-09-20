package net.muratov.assistant

import android.content.pm.ActivityInfo
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class OrientationDeviceTest {
    @Test fun appliesDeviceRotationPolicy() {
        val device = InstrumentationRegistry.getArguments().getString("orientationDevice")
        assumeTrue(device == "phone" || device == "fold")
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                assertEquals(if (device == "phone") ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
                    else ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED, activity.requestedOrientation)
            }
        }
    }
}
