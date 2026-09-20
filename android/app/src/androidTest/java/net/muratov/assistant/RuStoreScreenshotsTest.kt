package net.muratov.assistant

import android.graphics.Bitmap
import android.os.Build
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import net.muratov.assistant.data.ApiSession
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Opt-in capture of real UI. All API replies are synthetic and stay in the test APK. */
@RunWith(AndroidJUnit4::class)
class RuStoreScreenshotsTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private fun capture(name: String) {
        instrumentation.runOnMainSync {
            androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry.getInstance()
                .getActivitiesInStage(androidx.test.runner.lifecycle.Stage.RESUMED)
                .forEach { activity ->
                    activity.window.attributes = activity.window.attributes.apply {
                        layoutInDisplayCutoutMode = android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                    }
                    @Suppress("DEPRECATION")
                    activity.window.decorView.systemUiVisibility = 5894
                    activity.window.insetsController?.hide(android.view.WindowInsets.Type.systemBars())
                }
        }
        compose.waitForIdle()
        Thread.sleep(4_000) // Let transient system bars and window animations disappear.
        val bitmap = checkNotNull(instrumentation.uiAutomation.takeScreenshot())
        val directory = File(instrumentation.targetContext.filesDir, "rustore-screenshots").apply { mkdirs() }
        File(directory, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
    }

    @Test fun captureStoreScreenshots() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("rustoreScreenshots") == "true")
        assumeTrue(Build.FINGERPRINT.contains("generic") || Build.MODEL.startsWith("sdk_"))
        val app = instrumentation.targetContext.applicationContext as ImproverApplication
        val previousLanguage = net.muratov.assistant.i18n.Language.preference
        net.muratov.assistant.i18n.Language.set(app, "ru")
        val fixtures = JSONObject(instrumentation.context.assets.open("rustore/fixtures.json").bufferedReader().use { it.readText() })
        val delegations = fixtures.getJSONArray("delegations")
        val session = ApiSession { _, _ ->
            OkHttpClient.Builder().addInterceptor { chain ->
                val request = chain.request()
                val path = request.url.encodedPath
                val body: Any = when {
                    path == "/api/v1/tasks" -> fixtures.getJSONArray("tasks")
                    path == "/api/v1/plans/today" -> JSONObject("""{"id":"demo","plan_date":"2026-09-20","generated_at":"2026-09-20T09:00:00Z","items":[],"meetings":[]}""")
                    path == "/api/v1/delegations" -> {
                        val assignee = request.url.queryParameter("assignee").orEmpty()
                        val items = JSONArray()
                        for (i in 0 until delegations.length()) {
                            val d = delegations.getJSONObject(i)
                            if (assignee.isBlank() || d.getString("assignee_email") == assignee) items.put(d)
                        }
                        JSONObject().put("items", items).put("recipients", fixtures.getJSONArray("recipients")).put("has_more", false)
                    }
                    path.startsWith("/api/v1/delegations/") -> delegations.getJSONObject(0)
                    path == "/api/v1/system/status" -> JSONObject("""{"overall_status":"OK","generated_at":"2026-09-20T09:00:00Z","components":[]}""")
                    path == "/api/v1/relationships" -> JSONObject("""{"managers":[],"reports":[]}""")
                    path == "/api/v1/devices" -> JSONObject()
                    else -> JSONObject("""{"items":[],"offset":0,"limit":30,"has_more":false}""")
                }
                Response.Builder().request(request).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                    .body(body.toString().toResponseBody("application/json".toMediaType())).build()
            }.build()
        }
        val field = app.container.repository.javaClass.getDeclaredField("apiSession").apply { isAccessible = true }
        val previousSession = field.get(app.container.repository)
        val previousUrl = app.container.settings.serverUrl
        val previousAlias = app.container.settings.certificateAlias
        app.container.settings.serverUrl = "https://demo.invalid"
        app.container.settings.certificateAlias = null
        field.set(app.container.repository, session)
        try {
            ActivityScenario.launch(MainActivity::class.java).use {
                compose.waitUntil(15_000) { compose.onAllNodesWithText("Согласовать план запуска проекта").fetchSemanticsNodes().isNotEmpty() }
                capture("01-tasks")
                compose.onNodeWithText("Поручения").performClick()
                compose.waitUntil(15_000) { compose.onAllNodesWithText("Подготовить презентацию проекта").fetchSemanticsNodes().isNotEmpty() }
                capture("02-delegations")
                compose.onNodeWithText("Фильтры").performClick()
                compose.onNodeWithText("Исполнитель: Все исполнители").performClick()
                capture("03-recipient-filter")
                compose.onNodeWithText("Анна Смирнова · anna@example.com").performClick()
                compose.onNodeWithText("Готово").performClick()
                compose.waitUntil(15_000) { compose.onAllNodesWithText("Согласовать бюджет запуска").fetchSemanticsNodes().isEmpty() }
                capture("04-filtered-delegations")
                compose.onNodeWithText("Подготовить презентацию проекта").performClick()
                compose.waitUntil(15_000) { compose.onAllNodesWithText("Ожидаемый результат").fetchSemanticsNodes().isNotEmpty() }
                capture("05-delegation-details")
                compose.onNodeWithText("Назад").performClick()
            }
        } finally {
            net.muratov.assistant.i18n.Language.set(app, previousLanguage)
            field.set(app.container.repository, previousSession)
            session.close()
            app.container.settings.serverUrl = previousUrl
            app.container.settings.certificateAlias = previousAlias
        }
    }
}
