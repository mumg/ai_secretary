package net.muratov.assistant.data.local

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "tasks")
data class TaskEntity(
    @PrimaryKey val id: String,
    val title: String,
    val description: String?,
    val status: String,
    val priority: String,
    val dueAt: String?,
    val rankingScore: Double,
    val rankingReasons: String,
    val evidence: String?,
    val confidence: Double?,
    val sourceEventId: String?,
    val hasActiveReminder: Boolean,
    val updatedAt: String,
)
