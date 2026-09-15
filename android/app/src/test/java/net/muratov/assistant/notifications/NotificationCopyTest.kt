package net.muratov.assistant.notifications

import net.muratov.assistant.data.remote.TaskDto
import net.muratov.assistant.data.remote.ChatRequestDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationCopyTest {
    @Test
    fun taskPushRendersFromPayloadWithoutApiAccess() {
        val copy = notificationCopy("NEW_TASK", null, "Оценить эпики", "Предоставить\nобратную связь")
        assertEquals("Новая задача", copy.title)
        assertEquals("Оценить эпики", copy.text)
        assertEquals("Оценить эпики\nПредоставить обратную связь", copy.expandedText)
    }

    @Test
    fun meetingPreparationHasItsOwnNotification() {
        val copy = notificationCopy("MEETING_CONTEXT_READY", null)
        assertEquals("Контекст встречи готов", copy.title)
        assertTrue(copy.text.contains("встречи"))
    }
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

    @Test
    fun chatResponseIncludesQuestionAndAnswer() {
        val request = ChatRequestDto(
            id = "request-id",
            query = "О чём договорились?",
            status = "COMPLETED",
            answer = "Согласовали срок до пятницы.",
            references = emptyList(),
            error = null,
            attempts = 1,
            createdAt = "2026-09-13T00:00:00Z",
            updatedAt = "2026-09-13T00:00:01Z",
            completedAt = "2026-09-13T00:00:01Z",
        )

        val copy = chatNotificationCopy(request)

        assertEquals("Ответ Qwen готов", copy.title)
        assertEquals("Согласовали срок до пятницы.", copy.text)
        assertTrue(copy.expandedText.contains("О чём договорились?"))
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
