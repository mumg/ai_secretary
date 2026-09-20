package net.muratov.assistant.data.remote

import com.google.gson.annotations.SerializedName

data class DelegationDto(
    val id: String, val title: String, val description: String?, val status: String,
    @SerializedName("expected_result") val expectedResult: String?,
    @SerializedName("assignee_name") val assigneeName: String,
    @SerializedName("assignee_email") val assigneeEmail: String,
    @SerializedName("due_at") val dueAt: String?,
    @SerializedName("source_event_id") val sourceEventId: String?,
    val evidence: String, val history: List<DelegationChange> = emptyList(),
)
data class DelegationChange(val id: String, @SerializedName("old_status") val oldStatus: String?, @SerializedName("new_status") val newStatus: String, val actor: String, val explanation: String, @SerializedName("created_at") val createdAt: String, @SerializedName("source_event_id") val sourceEventId: String?)
data class DelegationRecipient(@SerializedName("assignee_name") val name: String, @SerializedName("assignee_email") val email: String) { val key: String get() = email.ifBlank { name } }
data class DelegationPage(val items: List<DelegationDto>, val recipients: List<DelegationRecipient>, @SerializedName("has_more") val hasMore: Boolean)
data class EmployeeDto(val name: String, val emails: List<String>)
data class RelationshipsDto(val managers: List<EmployeeDto> = emptyList(), val reports: List<EmployeeDto> = emptyList())
