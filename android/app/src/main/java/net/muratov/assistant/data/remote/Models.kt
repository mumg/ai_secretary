package net.muratov.assistant.data.remote

import com.google.gson.annotations.SerializedName

data class ReminderDto(
    val id: String,
    @SerializedName("remind_at") val remindAt: String,
    @SerializedName("sent_at") val sentAt: String?,
    val enabled: Boolean,
)

data class TaskDto(
    val id: String,
    val title: String,
    val description: String?,
    val status: String,
    val priority: String,
    @SerializedName("due_at") val dueAt: String?,
    @SerializedName("source_event_id") val sourceEventId: String?,
    val evidence: String?,
    val confidence: Double?,
    @SerializedName("ranking_score") val rankingScore: Double,
    @SerializedName("ranking_reasons") val rankingReasons: List<String>,
    val reminders: List<ReminderDto>,
    @SerializedName("updated_at") val updatedAt: String,
)

data class TaskSourceDto(
    val id: String,
    @SerializedName("source_id") val sourceId: String,
    @SerializedName("source_label") val sourceLabel: String,
    @SerializedName("source_type") val sourceType: String,
    @SerializedName("event_type") val eventType: String,
    val direction: String,
    val subject: String?,
    val author: String?,
    val participants: List<Map<String, String>>,
    @SerializedName("occurred_at") val occurredAt: String,
    val body: String,
    @SerializedName("source_url") val sourceUrl: String?,
)

data class TaskDetailDto(
    val task: TaskDto,
    val source: TaskSourceDto?,
)

data class PlanItemDto(
    val position: Int,
    val pinned: Boolean,
    @SerializedName("automatically_added") val automaticallyAdded: Boolean,
    val task: TaskDto,
)

data class DailyPlanDto(
    val id: String,
    @SerializedName("plan_date") val planDate: String,
    @SerializedName("generated_at") val generatedAt: String,
    val items: List<PlanItemDto>,
    val meetings: List<MeetingDto> = emptyList(),
)

data class MeetingDto(
    val id: String,
    @SerializedName("source_id") val sourceId: String,
    @SerializedName("source_label") val sourceLabel: String?,
    @SerializedName("source_event_id") val sourceEventId: String,
    val title: String,
    @SerializedName("starts_at") val startsAt: String,
    @SerializedName("ends_at") val endsAt: String,
    @SerializedName("all_day") val allDay: Boolean,
    val location: String?,
    val organizer: Map<String, String>?,
    val attendees: List<Map<String, String>>,
    val status: String,
    val method: String,
)

data class MeetingContextDto(
    val meeting: MeetingDto,
    val status: String,
    val summary: String?,
    val references: List<ChatReferenceDto>,
    @SerializedName("generated_at") val generatedAt: String?,
    val error: String?,
)

data class MeetingPageDto(
    val items: List<MeetingDto>,
    val offset: Int,
    val limit: Int,
    @SerializedName("has_more") val hasMore: Boolean,
)

data class MeetingResultDto(
    val id: String,
    @SerializedName("source_id") val sourceId: String,
    @SerializedName("source_label") val sourceLabel: String,
    @SerializedName("source_event_id") val sourceEventId: String,
    @SerializedName("calendar_meeting_id") val calendarMeetingId: String?,
    val title: String,
    @SerializedName("starts_at") val startsAt: String,
    @SerializedName("ends_at") val endsAt: String,
    @SerializedName("owner_name") val ownerName: String?,
    @SerializedName("meeting_url") val meetingUrl: String?,
    @SerializedName("transcript_status") val transcriptStatus: String,
    val summary: String?,
    val decisions: List<String>,
    val agreements: List<String>,
    @SerializedName("analysis_state") val analysisState: String,
    @SerializedName("analyzed_at") val analyzedAt: String?,
    @SerializedName("origin_type") val originType: String,
    @SerializedName("supplement_count") val supplementCount: Int,
    @SerializedName("brief_summary") val briefSummary: String,
)

data class ParticipantMeetingSummaryDto(
    @SerializedName("source_event_id") val sourceEventId: String,
    @SerializedName("source_label") val sourceLabel: String,
    val author: String?,
    @SerializedName("occurred_at") val occurredAt: String,
    val summary: String,
    val decisions: List<String>,
    val agreements: List<String>,
)

data class MeetingResultDetailDto(
    val id: String,
    @SerializedName("source_id") val sourceId: String,
    @SerializedName("source_label") val sourceLabel: String,
    @SerializedName("source_event_id") val sourceEventId: String,
    @SerializedName("calendar_meeting_id") val calendarMeetingId: String?,
    val title: String,
    @SerializedName("starts_at") val startsAt: String,
    @SerializedName("ends_at") val endsAt: String,
    @SerializedName("owner_name") val ownerName: String?,
    @SerializedName("meeting_url") val meetingUrl: String?,
    @SerializedName("transcript_status") val transcriptStatus: String,
    val summary: String?,
    val decisions: List<String>,
    val agreements: List<String>,
    @SerializedName("analysis_state") val analysisState: String,
    @SerializedName("analyzed_at") val analyzedAt: String?,
    @SerializedName("origin_type") val originType: String,
    @SerializedName("supplement_count") val supplementCount: Int,
    @SerializedName("brief_summary") val briefSummary: String,
    val participants: List<Map<String, String>>,
    @SerializedName("participant_summaries")
    val participantSummaries: List<ParticipantMeetingSummaryDto>,
)

data class MeetingResultPageDto(
    val items: List<MeetingResultDto>,
    val offset: Int,
    val limit: Int,
    @SerializedName("has_more") val hasMore: Boolean,
)

data class CreateTaskRequest(
    val title: String,
    val description: String? = null,
    val priority: String,
    @SerializedName("due_at") val dueAt: String? = null,
)

data class UpdateTaskRequest(
    val priority: String,
    @SerializedName("due_at") val dueAt: String?,
)

data class CreateTaskFromTextRequest(val text: String)

data class CreateReminderRequest(@SerializedName("remind_at") val remindAt: String)

data class DeviceRequest(
    val label: String,
    @SerializedName("fcm_token") val fcmToken: String,
)

data class ChatHistoryMessageDto(
    val role: String,
    val content: String,
)

data class ChatQueryRequest(
    val query: String,
    val history: List<ChatHistoryMessageDto>,
    @SerializedName("tag_ids") val tagIds: List<String> = emptyList(),
)

data class ChatReferenceDto(
    val key: String,
    val kind: String,
    val id: String,
    val title: String,
    @SerializedName("source_label") val sourceLabel: String?,
    @SerializedName("occurred_at") val occurredAt: String?,
    val snippet: String,
    @SerializedName("source_url") val sourceUrl: String?,
    @SerializedName("meeting_result_id") val meetingResultId: String? = null,
)

data class ChatResponseDto(
    val answer: String,
    val references: List<ChatReferenceDto>,
)

data class ChatRequestDto(
    val id: String,
    val query: String,
    val status: String,
    val answer: String?,
    val references: List<ChatReferenceDto>,
    val error: String?,
    val attempts: Int,
    @SerializedName("created_at") val createdAt: String,
    @SerializedName("updated_at") val updatedAt: String,
    @SerializedName("completed_at") val completedAt: String?,
)

data class ChatStreamEventDto(
    val type: String,
    val message: String? = null,
    val delta: String? = null,
    val references: List<ChatReferenceDto>? = null,
)

data class EventDto(
    val id: String,
    @SerializedName("source_id") val sourceId: String,
    @SerializedName("source_type") val sourceType: String,
    @SerializedName("event_type") val eventType: String,
    val direction: String,
    val subject: String?,
    val author: String?,
    val participants: List<Map<String, String>>,
    @SerializedName("occurred_at") val occurredAt: String,
    val body: String,
    @SerializedName("source_url") val sourceUrl: String?,
)

data class ConversationThreadDto(
    val id: String,
    @SerializedName("source_id") val sourceId: String,
    @SerializedName("source_label") val sourceLabel: String,
    @SerializedName("source_type") val sourceType: String,
    val title: String?,
    val participants: List<Map<String, String>>,
    val summary: String?,
    @SerializedName("event_count") val eventCount: Int,
    @SerializedName("first_event_at") val firstEventAt: String,
    @SerializedName("last_event_at") val lastEventAt: String,
    @SerializedName("summarized_at") val summarizedAt: String?,
)

data class ConversationThreadPageDto(
    val items: List<ConversationThreadDto>,
    val offset: Int,
    val limit: Int,
    @SerializedName("has_more") val hasMore: Boolean,
)

data class ConversationEventDto(
    val id: String,
    @SerializedName("event_type") val eventType: String,
    val direction: String,
    val subject: String?,
    val author: String?,
    @SerializedName("occurred_at") val occurredAt: String,
    val preview: String,
    @SerializedName("source_url") val sourceUrl: String?,
)

data class ConversationThreadDetailDto(
    val id: String,
    @SerializedName("source_id") val sourceId: String,
    @SerializedName("source_label") val sourceLabel: String,
    @SerializedName("source_type") val sourceType: String,
    val title: String?,
    val participants: List<Map<String, String>>,
    val summary: String?,
    @SerializedName("event_count") val eventCount: Int,
    @SerializedName("first_event_at") val firstEventAt: String,
    @SerializedName("last_event_at") val lastEventAt: String,
    @SerializedName("summarized_at") val summarizedAt: String?,
    val events: List<ConversationEventDto>,
    @SerializedName("has_more_events") val hasMoreEvents: Boolean,
)

data class ComponentStatusDto(
    val id: String,
    val label: String,
    @SerializedName("component_type") val componentType: String,
    val status: String,
    val message: String?,
    val metrics: Map<String, Double>,
    @SerializedName("observed_at") val observedAt: String,
    @SerializedName("expires_at") val expiresAt: String?,
)

data class SystemStatusDto(
    @SerializedName("overall_status") val overallStatus: String,
    @SerializedName("generated_at") val generatedAt: String,
    val components: List<ComponentStatusDto>,
)
