package net.muratov.assistant

import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.assertIsDisplayed
import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import net.muratov.assistant.data.ApiFactory
import net.muratov.assistant.data.remote.CreateTaskRequest
import net.muratov.assistant.notifications.RealtimeState
import kotlinx.coroutines.runBlocking
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

/** Opt-in: never creates test tasks on a configured production server. */
@RunWith(AndroidJUnit4::class)
class RealtimeLifecycleTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun liveChangesBackgroundDisconnectAndResumeCatchup() {
        val server = InstrumentationRegistry.getArguments().getString("realtimeTestServer")
        assumeTrue(server == "http://127.0.0.1:18768")
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as ImproverApplication
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            InstrumentationRegistry.getInstrumentation().uiAutomation.grantRuntimePermission(
                app.packageName, android.Manifest.permission.POST_NOTIFICATIONS,
            )
        }
        val previousServer = app.container.settings.serverUrl
        app.container.settings.serverUrl = server!!
        val api = ApiFactory.create(app, server, null)
        val created = mutableListOf<String>()
        try {
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                compose.waitUntil(15_000) { RealtimeState.connected.value }
                val title = "Live ${UUID.randomUUID().toString().take(8)}"
                created += runBlocking { api.createTask(CreateTaskRequest(title, priority = "NORMAL")).id }
                // No pull, navigation or manual refresh after the server mutation.
                compose.waitUntil(10_000) {
                    runCatching { compose.onAllNodes(androidx.compose.ui.test.hasText(title)).fetchSemanticsNodes().isNotEmpty() }.getOrDefault(false)
                }
                compose.onNodeWithText(title).assertIsDisplayed()

                scenario.moveToState(Lifecycle.State.CREATED)
                compose.waitUntil(5000) { !RealtimeState.active.value && !RealtimeState.connected.value }
                val missed = "Background ${UUID.randomUUID().toString().take(8)}"
                created += runBlocking { api.createTask(CreateTaskRequest(missed, priority = "HIGH")).id }
                scenario.moveToState(Lifecycle.State.RESUMED)
                compose.waitUntil(15_000) { RealtimeState.connected.value }
                compose.waitUntil(10_000) {
                    runCatching { compose.onAllNodes(androidx.compose.ui.test.hasText(missed)).fetchSemanticsNodes().isNotEmpty() }.getOrDefault(false)
                }
                compose.onNodeWithText(missed).assertIsDisplayed()

                // Invalid connection, then restored settings must replace the old
                // socket and ignore late callbacks without requiring app restart.
                app.container.settings.serverUrl = "http://127.0.0.1:18769"
                app.container.realtime.connectionSettingsChanged()
                compose.waitUntil(5000) { !RealtimeState.connected.value }
                app.container.settings.serverUrl = server
                app.container.realtime.connectionSettingsChanged()
                compose.waitUntil(15_000) { RealtimeState.connected.value }
            }
            compose.waitUntil(5000) { !RealtimeState.active.value && !RealtimeState.connected.value }
        } finally {
            runBlocking { created.forEach { api.complete(it) } }
            app.container.settings.serverUrl = previousServer
            app.container.realtime.connectionSettingsChanged()
        }
    }
}
