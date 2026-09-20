package net.muratov.assistant.notifications

import net.muratov.assistant.i18n.tr

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import net.muratov.assistant.ChatActivity
import net.muratov.assistant.ImproverApplication
import net.muratov.assistant.MainActivity
import net.muratov.assistant.MeetingContextActivity
import net.muratov.assistant.R
import net.muratov.assistant.TaskDetailActivity
import net.muratov.assistant.data.remote.ChatRequestDto
import net.muratov.assistant.data.remote.TaskDto

private val taskNotificationTypes = setOf(
    "CRITICAL_TASK",
    "NEW_TASK",
    "TASK_CONFIRMATION_REQUIRED",
    "TASK_REMINDER",
    "TASK_DUE_SOON",
    "TASK_OVERDUE",
    "TASK_POSSIBLY_COMPLETED",
)

internal data class NotificationCopy(
    val title: String,
    val text: String,
    val expandedText: String,
)

internal fun notificationCopy(
    type: String,
    task: TaskDto?,
    payloadTitle: String? = null,
    payloadDescription: String? = null,
): NotificationCopy {
    val title = when (type) {
        "CRITICAL_TASK" -> tr("Критическая задача")
        "NEW_TASK" -> tr("Новая задача")
        "TASK_CONFIRMATION_REQUIRED" -> tr("Нужно подтвердить задачу")
        "TASK_REMINDER" -> tr("Напоминание")
        "TASK_DUE_SOON" -> tr("Скоро истекает срок")
        "TASK_OVERDUE" -> tr("Задача просрочена")
        "TASK_POSSIBLY_COMPLETED" -> tr("Возможно, задача выполнена")
        "DAILY_PLAN_READY" -> tr("План на сегодня готов")
        "MEETING_CONTEXT_READY" -> tr("Контекст встречи готов")
        else -> tr("Обновление задач")
    }
    val taskTitle = (task?.title ?: payloadTitle)?.trim()?.takeIf(String::isNotEmpty)?.take(180)
    val description = (task?.description ?: payloadDescription)
        ?.replace(Regex("\\s+"), " ")
        ?.trim()
        ?.takeIf(String::isNotEmpty)
        ?.take(360)
    val text = taskTitle ?: when (type) {
        "DAILY_PLAN_READY" -> tr("Откройте приложение, чтобы посмотреть план")
        "MEETING_CONTEXT_READY" -> tr("Нажмите, чтобы посмотреть результат подготовки встречи")
        else -> tr("Откройте приложение, чтобы посмотреть изменения")
    }
    val expandedText = listOfNotNull(taskTitle, description)
        .joinToString("\n")
        .ifBlank { text }
    return NotificationCopy(title, text, expandedText)
}

internal fun chatNotificationCopy(request: ChatRequestDto?): NotificationCopy {
    val answer = request?.answer
        ?.replace(Regex("\\s+"), " ")
        ?.trim()
        ?.takeIf(String::isNotEmpty)
    val query = request?.query?.trim()?.takeIf(String::isNotEmpty)
    val text = answer?.take(180) ?: tr("Откройте чат, чтобы посмотреть ответ")
    val expanded = listOfNotNull(
        query?.let { tr("Вопрос: {0}" , it.take(300)) },
        answer?.let { tr("Ответ: {0}" , it.take(800)) },
    ).joinToString("\n\n").ifBlank { text }
    return NotificationCopy(tr("Ответ Qwen готов"), text, expanded)
}

class ImproverMessagingService : FirebaseMessagingService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNewToken(token: String) {
        val repository = (application as ImproverApplication).container.repository
        scope.launch { runCatching { repository.registerFcmToken(token) } }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        message.data["notification_id"]?.let { id ->
            val seen = getSharedPreferences("gateway-notifications", MODE_PRIVATE)
            synchronized(ImproverMessagingService::class.java) {
                if (seen.contains(id)) return
                val edit = seen.edit().putLong(id, System.currentTimeMillis())
                seen.all.entries.sortedByDescending { (it.value as? Long) ?: 0 }.drop(199).forEach { edit.remove(it.key) }
                edit.commit()
            }
        }
        val type = message.data["type"] ?: "TASK_UPDATE"
        val objectId = message.data["object_id"] ?: type
        Log.i("SecretaryPush", "Received ${type} for ${objectId}")
        val repository = (application as ImproverApplication).container.repository
        RealtimeState.changed(listOf("all"))
        if (RealtimeState.active.value) {
            // FCM may race the transition into the foreground or reconnect.
            // Reconcile through the same stream without a duplicate system alert.
            return
        }
        if (type == "MEETING_CONTEXT_READY") {
            showNotification(type, objectId, null)
            return
        }
        if (type == "CHAT_RESPONSE_READY") {
            val request = runBlocking(Dispatchers.IO) {
                runCatching { repository.chatRequest(objectId) }.getOrNull()
            }
            ChatNotificationState.notifyChanged(objectId)
            if (!ChatNotificationState.visible) {
                showChatNotification(objectId, request)
            }
            return
        }
        // FCM gives this callback only a few seconds. Render from the payload;
        // the task and list are fetched when the user opens the application.
        showNotification(type, objectId, null, message.data)
    }

    private fun showNotification(
        type: String,
        objectId: String,
        task: TaskDto?,
        data: Map<String, String> = emptyMap(),
    ) {
        val manager = getSystemService(NotificationManager::class.java)
        val channelId = when (type) {
            "CRITICAL_TASK" -> "critical_tasks"
            "TASK_REMINDER", "TASK_DUE_SOON", "TASK_OVERDUE" -> "reminders"
            "TASK_POSSIBLY_COMPLETED" -> "completion"
            "MEETING_CONTEXT_READY" -> "meeting_context"
            else -> "task_updates"
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val name = when (channelId) {
                "critical_tasks" -> tr("Критические задачи")
                "reminders" -> tr("Напоминания")
                "completion" -> tr("Проверка выполнения")
                "meeting_context" -> tr("Подготовка к встречам")
                else -> tr("Обновления задач")
            }
            manager.createNotificationChannel(
                NotificationChannel(channelId, name, NotificationManager.IMPORTANCE_HIGH),
            )
        }
        val intent = if (type == "MEETING_CONTEXT_READY") {
            MeetingContextActivity.intent(this, objectId)
        } else if (type in taskNotificationTypes) {
            TaskDetailActivity.intent(this, objectId)
        } else {
            Intent(this, MainActivity::class.java)
        }
        val pendingIntent = PendingIntent.getActivity(
            this,
            objectId.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val copy = notificationCopy(type, task, data["task_title"], data["task_description"])
        val notification = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle(copy.title)
            .setContentText(copy.text)
            .setSubText(tr("AI Секретарь"))
            .setStyle(NotificationCompat.BigTextStyle().bigText(copy.expandedText))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .build()
        manager.notify(objectId.hashCode(), notification)
        Log.i("SecretaryPush", "Displayed ${type} for ${objectId}")
    }

    private fun showChatNotification(objectId: String, request: ChatRequestDto?) {
        val manager = getSystemService(NotificationManager::class.java)
        val channelId = "chat_responses"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(
                    channelId,
                    tr("Ответы Qwen"),
                    NotificationManager.IMPORTANCE_HIGH,
                ),
            )
        }
        val pendingIntent = PendingIntent.getActivity(
            this,
            objectId.hashCode(),
            Intent(this, ChatActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val copy = chatNotificationCopy(request)
        val notification = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(copy.title)
            .setContentText(copy.text)
            .setSubText(tr("AI Секретарь"))
            .setStyle(NotificationCompat.BigTextStyle().bigText(copy.expandedText))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .build()
        manager.notify(objectId.hashCode(), notification)
    }
}
