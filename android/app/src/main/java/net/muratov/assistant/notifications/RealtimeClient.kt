package net.muratov.assistant.notifications

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import net.muratov.assistant.data.ApiFactory
import net.muratov.assistant.data.SettingsStore
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.random.Random

/** Single socket while at least one Activity is visible, including detail screens. */
class RealtimeClient(
    private val application: Application,
    private val settings: SettingsStore,
    private val registerDevice: suspend () -> Unit,
) : Application.ActivityLifecycleCallbacks {
    private val registrationScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var registrationJob: Job? = null
    private val handler = Handler(Looper.getMainLooper())
    private var visibleActivities = 0
    private var socket: WebSocket? = null
    private var client: OkHttpClient? = null
    private var attempt = 0
    private var generation = 0
    private var lastMessage = 0L
    private val retry = Runnable { connect() }
    private val watchdog = object : Runnable {
        override fun run() {
            if (!RealtimeState.active.value) return
            if (socket != null && android.os.SystemClock.elapsedRealtime() - lastMessage > 60_000) {
                disconnect()
                scheduleRetry()
            }
            handler.postDelayed(this, 10_000)
        }
    }
    private val fallback = object : Runnable {
        override fun run() {
            if (!RealtimeState.active.value) return
            if (!RealtimeState.connected.value) RealtimeState.changed(listOf("all"))
            handler.postDelayed(this, 30_000)
        }
    }

    fun install() { application.registerActivityLifecycleCallbacks(this) }

    fun connectionSettingsChanged() {
        handler.post {
            disconnect()
            attempt = 0
            if (RealtimeState.active.value) {
                connect()
                registerPushToken()
            }
        }
    }

    private fun registerPushToken() {
        registrationJob?.cancel()
        registrationJob = registrationScope.launch {
            while (RealtimeState.active.value) {
                try {
                    registerDevice()
                    Log.i("SecretaryPush", "Device registration succeeded")
                    return@launch
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (failure: Exception) {
                    Log.w("SecretaryPush", "Device registration failed: ${failure.javaClass.simpleName}")
                    delay(30_000)
                }
            }
        }
    }

    private fun disconnect() {
        generation++
        handler.removeCallbacks(retry)
        val previous = socket
        socket = null
        RealtimeState.connected.value = false
        // cancel closes transport immediately; no background close handshake.
        previous?.cancel()
        client?.connectionPool?.evictAll()
        client?.dispatcher?.executorService?.shutdown()
        client = null
    }

    private fun scheduleRetry() {
        if (!RealtimeState.active.value) return
        handler.removeCallbacks(retry)
        val delay = (1000L shl attempt.coerceAtMost(5)).coerceAtMost(30_000)
        attempt++
        handler.postDelayed(retry, (delay * Random.nextDouble(0.8, 1.2)).toLong())
    }

    private fun connect() {
        if (!RealtimeState.active.value || socket != null) return
        val current = ++generation
        try {
            val base = settings.serverUrl.trimEnd('/')
            val transport = ApiFactory.client(application, base, settings.certificateAlias)
                .newBuilder().pingInterval(20, TimeUnit.SECONDS).build()
            client = transport
            val request = Request.Builder().url("$base/api/v1/realtime").build()
            lastMessage = android.os.SystemClock.elapsedRealtime()
            socket = transport.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    handler.post {
                        if (current != generation || !RealtimeState.active.value) {
                            webSocket.cancel()
                        } else RealtimeState.connected.value = true
                    }
                }
                override fun onMessage(webSocket: WebSocket, text: String) {
                    handler.post {
                        if (current != generation || !RealtimeState.active.value) return@post
                        lastMessage = android.os.SystemClock.elapsedRealtime()
                        runCatching {
                            val message = JSONObject(text)
                            when (message.optString("type")) {
                                "ping" -> webSocket.send("pong")
                                "changed" -> {
                                    val topics = message.getJSONArray("topics")
                                    RealtimeState.changed((0 until topics.length()).map { topics.getString(it) })
                                    attempt = 0
                                }
                            }
                        }
                    }
                }
                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(code, reason)
                }
                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = failed()
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = failed()
                private fun failed() {
                    handler.post {
                        if (current != generation) return@post
                        disconnect()
                        scheduleRetry()
                    }
                }
            })
        } catch (_: Exception) {
            disconnect()
            scheduleRetry()
        }
    }

    override fun onActivityStarted(activity: Activity) {
        visibleActivities++
        if (visibleActivities == 1) {
            RealtimeState.active.value = true
            RealtimeState.changed(listOf("all"))
            connect()
            registerPushToken()
            handler.postDelayed(watchdog, 10_000)
            handler.postDelayed(fallback, 30_000)
        }
    }
    override fun onActivityStopped(activity: Activity) {
        visibleActivities = (visibleActivities - 1).coerceAtLeast(0)
        if (visibleActivities == 0) {
            RealtimeState.active.value = false
            handler.removeCallbacks(watchdog)
            handler.removeCallbacks(fallback)
            disconnect()
            registrationJob?.cancel()
        }
    }
    override fun onActivityCreated(activity: Activity, state: Bundle?) = Unit
    override fun onActivityResumed(activity: Activity) = Unit
    override fun onActivityPaused(activity: Activity) = Unit
    override fun onActivitySaveInstanceState(activity: Activity, state: Bundle) = Unit
    override fun onActivityDestroyed(activity: Activity) = Unit
}
