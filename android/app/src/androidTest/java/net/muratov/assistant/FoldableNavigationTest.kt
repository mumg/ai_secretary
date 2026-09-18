package net.muratov.assistant

import android.app.Activity
import android.content.Intent
import android.os.Build
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import androidx.window.embedding.ActivityEmbeddingController
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.Rule
import org.junit.runner.RunWith

/** Opt-in on a Pixel Fold emulator; uses an unreachable loopback endpoint, never production. */
@RunWith(AndroidJUnit4::class)
class FoldableNavigationTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private fun shell(command: String) {
        instrumentation.uiAutomation.executeShellCommand(command).use { descriptor ->
            android.os.ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { it.readBytes() }
        }
    }

    private fun activities(): List<Activity> {
        var result = emptyList<Activity>()
        instrumentation.runOnMainSync {
            result = listOf(Stage.RESUMED, Stage.STARTED, Stage.PAUSED, Stage.STOPPED)
                .flatMap { ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(it) }
                .filterNot { it.isFinishing || it.isDestroyed }
        }
        return result
    }

    private fun await(description: String, condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 15_000
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return
            Thread.sleep(100)
        }
        fail("Timed out: $description; activities=${activities().map { it.javaClass.simpleName }}")
    }

    private fun embedded(type: Class<out Activity>): Boolean {
        var result = false
        instrumentation.runOnMainSync {
            val activity = ActivityLifecycleMonitorRegistry.getInstance()
                .getActivitiesInStage(Stage.RESUMED).firstOrNull { type.isInstance(it) }
            if (activity != null) result = ActivityEmbeddingController.getInstance(activity).isActivityEmbedded(activity)
        }
        return result
    }

    @Test fun foldUnfoldAndNestedDetailsKeepSelectionAndBackStack() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("foldableTest") == "true")
        assumeTrue(Build.FINGERPRINT.contains("generic") || Build.MODEL.startsWith("sdk_"))
        val app = instrumentation.targetContext.applicationContext as ImproverApplication
        val previousServer = app.container.settings.serverUrl
        app.container.settings.serverUrl = "https://127.0.0.1:1"
        if (Build.VERSION.SDK_INT >= 33) {
            instrumentation.uiAutomation.grantRuntimePermission(app.packageName, "android.permission.POST_NOTIFICATIONS")
        }
        try {
            shell("cmd device_state state 2")
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                await("unfolded list and placeholder") {
                    embedded(MainActivity::class.java) && embedded(DetailPlaceholderActivity::class.java)
                }
                shell("cmd device_state state 0")
                await("folded list, placeholder removed") {
                    !embedded(MainActivity::class.java) && activities().none { it is DetailPlaceholderActivity }
                }
                scenario.onActivity { it.startActivity(TaskDetailActivity.intent(it, "fold-task-1")) }
                await("task opened on cover display") { activities().any { it is TaskDetailActivity } }
                assertFalse(embedded(TaskDetailActivity::class.java))
                shell("cmd device_state state 2")
                await("selected task moves next to list") {
                    embedded(MainActivity::class.java) && embedded(TaskDetailActivity::class.java)
                }
                assertEquals("fold-task-1", activities().filterIsInstance<TaskDetailActivity>().single().intent.getStringExtra("task_id"))
                scenario.onActivity { it.startActivity(TaskDetailActivity.intent(it, "fold-task-2")) }
                await("new selection replaces previous detail") {
                    val details = activities().filterIsInstance<TaskDetailActivity>()
                    details.size == 1 && details.single().intent.getStringExtra("task_id") == "fold-task-2"
                }
                shell("cmd device_state state 1")
                await("half-open detail remains embedded") { embedded(TaskDetailActivity::class.java) }
                shell("cmd device_state state 0")
                await("selected task fills cover display") { !embedded(TaskDetailActivity::class.java) }
                assertEquals("fold-task-2", activities().filterIsInstance<TaskDetailActivity>().single().intent.getStringExtra("task_id"))
                shell("cmd device_state state 2")
                await("reopened task split") { embedded(TaskDetailActivity::class.java) }
                scenario.onActivity { it.startActivity(ConversationThreadDetailActivity.intent(it, "fold-thread")) }
                await("thread replaces task") { embedded(ConversationThreadDetailActivity::class.java) }
                instrumentation.runOnMainSync {
                    val thread = ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED)
                        .filterIsInstance<ConversationThreadDetailActivity>().single()
                    thread.startActivity(EventDetailActivity.intent(thread, "fold-event", "Test"))
                }
                await("nested message keeps list visible") { embedded(MainActivity::class.java) && embedded(EventDetailActivity::class.java) }
                shell("input keyevent KEYCODE_BACK")
                await("back returns to thread in detail pane") { embedded(ConversationThreadDetailActivity::class.java) && activities().none { it is EventDetailActivity } }
                scenario.onActivity { it.startActivity(Intent(it, ChatActivity::class.java)) }
                await("chat in detail pane") { embedded(ChatActivity::class.java) }
                compose.onNodeWithText("Вопрос по задачам и переписке").performTextInput("Unsent foldable draft")
                shell("cmd device_state state 0")
                await("folded chat") { !embedded(ChatActivity::class.java) }
                compose.onNodeWithText("Unsent foldable draft").assertIsDisplayed()
                shell("cmd device_state state 2")
                await("unfolded chat") { embedded(ChatActivity::class.java) }
                compose.onNodeWithText("Unsent foldable draft").assertIsDisplayed()
            }
        } finally {
            instrumentation.runOnMainSync {
                Stage.entries.filter { it != Stage.DESTROYED }.flatMap {
                    ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(it)
                }.toSet().forEach { it.finish() }
            }
            app.container.settings.serverUrl = previousServer
            shell("cmd device_state state reset")
        }
    }
}
