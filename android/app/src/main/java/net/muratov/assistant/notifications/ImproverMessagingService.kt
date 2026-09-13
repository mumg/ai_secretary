package net.muratov.assistant.notifications

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import net.muratov.assistant.ImproverApplication
import net.muratov.assistant.MainActivity
import net.muratov.assistant.R
import net.muratov.assistant.TaskDetailActivity
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

internal fun notificationCopy(type: String, task: TaskDto?): NotificationCopy {
    val title = when (type) {
        "CRITICAL_TASK" -> "Критическая задача"
        "NEW_TASK" -> "Новая задача"
        "TASK_CONFIRMATION_REQUIRED" -> "Нужно подтвердить задачу"
        "TASK_REMINDER" -> "Напоминание"
        "TASK_DUE_SOON" -> "Скоро истекает срок"
        "TASK_OVERDUE" -> "Задача просрочена"
        "TASK_POSSIBLY_COMPLETED" -> "Возможно, задача выполнена"
        "DAILY_PLAN_READY" -> "План на сегодня готов"
        else -> "Обновление задач"
    }
    val taskTitle = task?.title?.trim()?.takeIf(String::isNotEmpty)?.take(180)
    val description = task?.description
        ?.replace(Regex("\\s+"), " ")
        ?.trim()
        ?.takeIf(String::isNotEmpty)
        ?.take(360)
    val text = taskTitle ?: when (type) {
        "DAILY_PLAN_READY" -> "Откройте приложение, чтобы посмотреть план"
        else -> "Откройте приложение, чтобы посмотреть изменения"
    }
    val expandedText = listOfNotNull(taskTitle, description)
        .joinToString("\n")
        .ifBlank { text }
    return NotificationCopy(title, text, expandedText)
}

class ImproverMessagingService : FirebaseMessagingService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNewToken(token: String) {
        val repository = (application as ImproverApplication).container.repository
        scope.launch { runCatching { repository.registerFcmToken(token) } }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val type = message.data["type"] ?: "TASK_UPDATE"
        val objectId = message.data["object_id"] ?: type
        val repository = (application as ImproverApplication).container.repository
        val task = runBlocking(Dispatchers.IO) {
            coroutineScope {
                val detail = async {
                    if (type in taskNotificationTypes) {
                        runCatching { repository.detail(objectId).task }.getOrNull()
                    } else {
                        null
                    }
                }
                val refresh = async { runCatching { repository.refresh() } }
                detail.await().also { refresh.await() }
            }
        }
        showNotification(type, objectId, task)
    }

    private fun showNotification(type: String, objectId: String, task: TaskDto?) {
        val manager = getSystemService(NotificationManager::class.java)
        val channelId = when (type) {
            "CRITICAL_TASK" -> "critical_tasks"
            "TASK_REMINDER", "TASK_DUE_SOON", "TASK_OVERDUE" -> "reminders"
            "TASK_POSSIBLY_COMPLETED" -> "completion"
            else -> "task_updates"
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val name = when (channelId) {
                "critical_tasks" -> "Критические задачи"
                "reminders" -> "Напоминания"
                "completion" -> "Проверка выполнения"
                else -> "Обновления задач"
            }
            manager.createNotificationChannel(
                NotificationChannel(channelId, name, NotificationManager.IMPORTANCE_HIGH),
            )
        }
        val intent = if (task != null) {
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
        val copy = notificationCopy(type, task)
        val notification = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle(copy.title)
            .setContentText(copy.text)
            .setSubText(getString(R.string.app_name))
            .setStyle(NotificationCompat.BigTextStyle().bigText(copy.expandedText))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .build()
        manager.notify(objectId.hashCode(), notification)
    }
}
