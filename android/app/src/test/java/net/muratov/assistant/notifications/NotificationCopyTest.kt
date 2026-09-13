package net.muratov.assistant.notifications

import net.muratov.assistant.data.remote.TaskDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationCopyTest {
    @Test
    fun criticalTaskIncludesTitleAndCompactDescription() {
        val task = task(
            title = "Проверить push",
            description = "Краткое   описание\nзадачи",
        )

        val copy = notificationCopy("CRITICAL_TASK", task)

        assertEquals("Критическая задача", copy.title)
        assertEquals("Проверить push", copy.text)
        assertEquals("Проверить push\nКраткое описание задачи", copy.expandedText)
    }

    @Test
    fun missingTaskUsesSafeFallback() {
        val copy = notificationCopy("TASK_OVERDUE", null)

        assertEquals("Задача просрочена", copy.title)
        assertTrue(copy.text.contains("Откройте приложение"))
    }

    private fun task(title: String, description: String?) = TaskDto(
        id = "task-id",
        title = title,
        description = description,
        status = "NEW",
        priority = "CRITICAL",
        dueAt = null,
        sourceEventId = null,
        evidence = null,
        confidence = null,
        rankingScore = 0.0,
        rankingReasons = emptyList(),
        reminders = emptyList(),
        updatedAt = "2026-09-12T00:00:00Z",
    )
}
