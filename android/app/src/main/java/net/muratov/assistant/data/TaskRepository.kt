package net.muratov.assistant.data

import net.muratov.assistant.i18n.tr

import android.content.Context
import android.os.Build
import com.google.firebase.messaging.FirebaseMessaging
import com.google.gson.Gson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import net.muratov.assistant.security.GatewayIdentity
import net.muratov.assistant.security.GatewayPush
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
import net.muratov.assistant.data.remote.ChatRequestDto
import net.muratov.assistant.data.remote.ChatResponseDto
import net.muratov.assistant.data.remote.ChatStreamEventDto
import net.muratov.assistant.data.remote.ConversationThreadPageDto
import net.muratov.assistant.data.remote.ConversationThreadDetailDto
import net.muratov.assistant.data.remote.DeviceRequest
import net.muratov.assistant.data.remote.EventDto
import net.muratov.assistant.data.remote.DailyPlanDto
import net.muratov.assistant.data.remote.MeetingContextDto
import net.muratov.assistant.data.remote.MeetingPageDto
import net.muratov.assistant.data.remote.MeetingResultPageDto
import net.muratov.assistant.data.remote.MeetingResultDetailDto
import net.muratov.assistant.data.remote.TaskDetailDto
import net.muratov.assistant.data.remote.TaskDto
import net.muratov.assistant.data.remote.UpdateTaskRequest
import net.muratov.assistant.data.remote.SystemStatusDto

class TaskRepository(
    private val context: Context,
    private val dao: TaskDao,
    private val settings: SettingsStore,
) {
    val tasks: Flow<List<TaskEntity>> = dao.observeActive()

    private val apiSession = ApiSession { url, alias -> ApiFactory.client(context, url, alias) }

    suspend fun delegations(query: String, assignee: String, status: String, due: String, offset: Int = 0, archive: Boolean = false) = api().delegations(query,assignee,status,due,offset,archive=if (archive) "true" else null)
    suspend fun delegation(id: String) = api().delegation(id)
    suspend fun delegationStatus(id: String, status: String) = api().delegationStatus(id,mapOf("status" to status))
    suspend fun relationships() = api().relationships()
    suspend fun saveRelationships(body: net.muratov.assistant.data.remote.RelationshipsDto) = api().saveRelationships(body)
    private fun api() = apiSession.get(settings.serverUrl, settings.certificateAlias)

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

    suspend fun searchTasks(query: String, archive: Boolean = false): List<TaskEntity> =
        api().tasks(query = query, archive = if (archive) "true" else null).map { it.toEntity() }

    suspend fun archivedTasks(): List<TaskEntity> =
        api().tasks(archive = "true").map { it.toEntity() }

    suspend fun detail(id: String): TaskDetailDto = api().task(id)

    suspend fun updateTask(id: String, priority: String, dueAt: String?) {
        api().updateTask(id, UpdateTaskRequest(priority, dueAt))
        refresh()
    }

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

    suspend fun meetingContext(id: String): MeetingContextDto = api().meetingContext(id)

    suspend fun refreshMeetingContext(id: String): MeetingContextDto = api().refreshMeetingContext(id)

    suspend fun meetingResult(id: String): MeetingResultDetailDto = api().meetingResult(id)

    suspend fun systemStatus(): SystemStatusDto = api().systemStatus()

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
        if (!settings.isConfigured) return
        val token = tokenOverride ?: FirebaseMessaging.getInstance().token.await()
        val alias = settings.certificateAlias
        if (GatewayIdentity.isAlias(alias)) {
            withContext(Dispatchers.IO) { GatewayPush.register(context, GatewayIdentity.load(context, settings.serverUrl, alias!!), token) }
        } else api().registerDevice(DeviceRequest(label = Build.MODEL, fcmToken = token))
    }

    suspend fun chat(
        query: String,
        history: List<ChatHistoryMessageDto>,
    ): ChatResponseDto = api().chat(ChatQueryRequest(query = query, history = history))

    suspend fun enqueueChat(
        query: String,
        history: List<ChatHistoryMessageDto>,
    ): ChatRequestDto = api().enqueueChat(ChatQueryRequest(query = query, history = history))

    suspend fun chatRequests(limit: Int = 100): List<ChatRequestDto> =
        api().chatRequests(limit)

    suspend fun chatRequest(id: String): ChatRequestDto = api().chatRequest(id)

    fun chatStream(
        query: String,
        history: List<ChatHistoryMessageDto>,
    ): Flow<ChatStreamEventDto> = flow {
        val response = api().chatStream(ChatQueryRequest(query = query, history = history))
        if (!response.isSuccessful) {
            val detail = response.errorBody()?.string()?.take(1000)
            error(detail ?: tr("Сервер вернул HTTP {0}" , response.code()))
        }
        val body = response.body() ?: error(tr("Сервер вернул пустой поток"))
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
