package net.muratov.assistant.data

import android.content.Context
import android.os.Build
import com.google.firebase.messaging.FirebaseMessaging
import com.google.gson.Gson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.tasks.await
import net.muratov.assistant.data.local.TaskDao
import net.muratov.assistant.data.local.TaskEntity
import net.muratov.assistant.data.remote.CreateReminderRequest
import net.muratov.assistant.data.remote.CreateTaskRequest
import net.muratov.assistant.data.remote.CreateTaskFromTextRequest
import net.muratov.assistant.data.remote.ChatHistoryMessageDto
import net.muratov.assistant.data.remote.ChatQueryRequest
import net.muratov.assistant.data.remote.ChatResponseDto
import net.muratov.assistant.data.remote.ChatStreamEventDto
import net.muratov.assistant.data.remote.ConversationThreadPageDto
import net.muratov.assistant.data.remote.ConversationThreadDetailDto
import net.muratov.assistant.data.remote.DeviceRequest
import net.muratov.assistant.data.remote.EventDto
import net.muratov.assistant.data.remote.DailyPlanDto
import net.muratov.assistant.data.remote.MeetingPageDto
import net.muratov.assistant.data.remote.MeetingResultPageDto
import net.muratov.assistant.data.remote.MeetingResultDetailDto
import net.muratov.assistant.data.remote.TaskDetailDto
import net.muratov.assistant.data.remote.TaskDto

class TaskRepository(
    private val context: Context,
    private val dao: TaskDao,
    private val settings: SettingsStore,
) {
    val tasks: Flow<List<TaskEntity>> = dao.observeActive()

    private fun api() = ApiFactory.create(context, settings.serverUrl, settings.certificateAlias)

    private fun TaskDto.toEntity() = TaskEntity(
        id = id,
        title = title,
        description = description,
        status = status,
        priority = priority,
        dueAt = dueAt,
        rankingScore = rankingScore,
        rankingReasons = rankingReasons.joinToString(" • "),
        evidence = evidence,
        confidence = confidence,
        sourceEventId = sourceEventId,
        hasActiveReminder = reminders.any { it.enabled && it.sentAt == null },
        updatedAt = updatedAt,
    )

    suspend fun refresh() {
        val remote = api().tasks()
        val entities = remote.map { it.toEntity() }
        dao.upsertAll(entities)
        if (entities.isEmpty()) {
            dao.removeAllActive()
        } else {
            dao.removeMissingActive(entities.map(TaskEntity::id))
        }
    }

    suspend fun searchTasks(query: String): List<TaskEntity> =
        api().tasks(query = query).map { it.toEntity() }

    suspend fun detail(id: String): TaskDetailDto = api().task(id)

    suspend fun event(id: String): EventDto = api().event(id)

    suspend fun threads(
        offset: Int,
        limit: Int = 20,
        query: String? = null,
    ): ConversationThreadPageDto = api().threads(offset, limit, query)

    suspend fun thread(id: String): ConversationThreadDetailDto = api().thread(id)

    suspend fun meetings(
        offset: Int,
        limit: Int = 20,
        query: String? = null,
    ): MeetingPageDto = api().meetings(offset, limit, query)

    suspend fun meetingResults(
        offset: Int,
        limit: Int = 20,
        query: String? = null,
    ): MeetingResultPageDto = api().meetingResults(offset, limit, query)

    suspend fun meetingResult(id: String): MeetingResultDetailDto = api().meetingResult(id)

    suspend fun today(refresh: Boolean = false): DailyPlanDto = api().today(refresh)

    suspend fun create(title: String, priority: String, dueAt: String?) {
        api().createTask(CreateTaskRequest(title = title, priority = priority, dueAt = dueAt))
        refresh()
    }

    suspend fun createFromText(text: String) {
        api().createTaskFromText(CreateTaskFromTextRequest(text))
        refresh()
    }

    suspend fun complete(id: String) {
        api().complete(id)
        refresh()
    }

    suspend fun confirm(id: String) {
        api().confirm(id)
        refresh()
    }

    suspend fun reject(id: String) {
        api().reject(id)
        refresh()
    }

    suspend fun addReminder(id: String, remindAt: String) {
        api().addReminder(id, CreateReminderRequest(remindAt))
        refresh()
    }

    suspend fun registerFcmToken(tokenOverride: String? = null) {
        val token = tokenOverride ?: FirebaseMessaging.getInstance().token.await()
        api().registerDevice(DeviceRequest(label = Build.MODEL, fcmToken = token))
    }

    suspend fun chat(
        query: String,
        history: List<ChatHistoryMessageDto>,
    ): ChatResponseDto = api().chat(ChatQueryRequest(query = query, history = history))

    fun chatStream(
        query: String,
        history: List<ChatHistoryMessageDto>,
    ): Flow<ChatStreamEventDto> = flow {
        val response = api().chatStream(ChatQueryRequest(query = query, history = history))
        if (!response.isSuccessful) {
            val detail = response.errorBody()?.string()?.take(1000)
            error(detail ?: "Сервер вернул HTTP ${response.code()}")
        }
        val body = response.body() ?: error("Сервер вернул пустой поток")
        val reader = body.charStream().buffered()
        try {
            while (true) {
                val line = reader.readLine() ?: break
                if (!line.startsWith("data:")) continue
                val json = line.removePrefix("data:").trim()
                if (json.isNotEmpty()) emit(Gson().fromJson(json, ChatStreamEventDto::class.java))
            }
        } finally {
            reader.close()
        }
    }.flowOn(Dispatchers.IO)
}
