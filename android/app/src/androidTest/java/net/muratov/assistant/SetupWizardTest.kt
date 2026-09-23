package net.muratov.assistant

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import net.muratov.assistant.data.SettingsStore
import net.muratov.assistant.data.ApiFactory
import net.muratov.assistant.setup.SetupWizard
import net.muratov.assistant.ui.ImproverTheme
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SetupWizardTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Test fun startsWithCameraScannerAndNoManualConnection() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        context.getSharedPreferences("connection", 0).edit().clear().commit()
        val settings = SettingsStore(context)
        assertTrue(runCatching { settings.saveConnection("https://assistant.example.org", null) }.isFailure)
        assertTrue(runCatching { ApiFactory.client(context, "https://assistant.example.org", null) }.isFailure)
        compose.setContent { ImproverTheme { SetupWizard(settings, onComplete = {}) } }
        compose.onNodeWithText("Сканировать QR-код").assertIsDisplayed()
        compose.onNodeWithText("Адрес сервера").assertDoesNotExist()
        compose.onNodeWithText("Далее").assertDoesNotExist()
        compose.onNodeWithText("Проверить и начать").assertDoesNotExist()
        assertFalse(settings.isConfigured)
    }

    @Test fun existingConnectionCannotBypassQRScanner() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        context.getSharedPreferences("connection", 0).edit().clear().commit()
        val settings = SettingsStore(context)
        settings.serverUrl = "https://assistant.example.org"
        compose.setContent { ImproverTheme {
            SetupWizard(settings, onComplete = { fail("Setup must require a new QR scan") })
        } }
        compose.onNodeWithText("Сканировать QR-код").assertIsDisplayed()
        compose.onNodeWithText("Проверить и начать").assertDoesNotExist()
        assertEquals("https://assistant.example.org", SettingsStore(context).serverUrl)
        assertFalse(SettingsStore(context).isConfigured)
        context.getSharedPreferences("connection", 0).edit().clear().commit()
    }
}
