package net.muratov.assistant

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import net.muratov.assistant.data.SettingsStore
import net.muratov.assistant.setup.SetupWizard
import net.muratov.assistant.ui.ImproverTheme
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SetupWizardTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Test fun startsBlankAndRejectsInvalidServer() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        context.getSharedPreferences("connection", 0).edit().clear().commit()
        val settings = SettingsStore(context)
        compose.setContent { ImproverTheme { SetupWizard(settings, onComplete = {}) } }
        compose.onNodeWithText("Шаг 1 из 3").assertIsDisplayed()
        compose.onNodeWithText("Далее").assertIsNotEnabled()
        compose.onNodeWithText("Адрес сервера").performTextInput("http://example.org")
        compose.onNodeWithText("Далее").assertIsNotEnabled()
        assertFalse(settings.isConfigured)
    }

    @Test fun savesOnlyAfterSuccessfulCheckAndCanRetry() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        context.getSharedPreferences("connection", 0).edit().clear().commit()
        val settings = SettingsStore(context)
        var completed = false
        var attempts = 0
        compose.setContent { ImproverTheme {
            SetupWizard(settings, onComplete = { completed = true }, checkConnection = { url, alias ->
                assertEquals("https://assistant.example.org", url)
                assertNull(alias)
                attempts++
                if (attempts == 1) throw java.io.IOException("Test connection refused")
            })
        } }
        compose.onNodeWithText("Адрес сервера").performTextInput("https://assistant.example.org/")
        compose.onNodeWithText("Далее").performClick()
        compose.onNodeWithText("Шаг 2 из 3").assertIsDisplayed()
        compose.onNodeWithText("Далее").performScrollTo().performClick()
        compose.onNodeWithText("Проверить и начать").performScrollTo().performClick()
        compose.waitForIdle()
        assertFalse(completed)
        assertFalse(settings.isConfigured)
        compose.onNodeWithText("Проверить и начать").performScrollTo().performClick()
        compose.waitUntil { completed }
        assertTrue(settings.isConfigured)
        assertEquals("https://assistant.example.org", SettingsStore(context).serverUrl)
        context.getSharedPreferences("connection", 0).edit().clear().commit()
    }
}
