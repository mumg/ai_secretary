package net.muratov.assistant

import com.google.gson.Gson
import net.muratov.assistant.data.remote.ChatReferenceDto
import net.muratov.assistant.data.remote.MeetingContextDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MeetingContextContractTest {
    @Test
    fun preparedContextPreservesCalendarAndPreviousResultNavigationIds() {
        val detail = Gson().fromJson("""
            {
              "meeting": {"id":"calendar-id", "source_event_id":"invitation-id",
                "source_id":"synthetic", "title":"Orion", "starts_at":"2026-09-15T09:00:00Z",
                "ends_at":"2026-09-15T10:00:00Z", "attendees":[], "status":"CONFIRMED"},
              "status":"READY", "summary":"Согласовали бюджет [E1]",
              "generated_at":"2026-09-14T09:00:00Z",
              "references":[{"key":"E1", "kind":"event", "id":"transcript-id",
                "meeting_result_id":"result-id", "title":"Orion", "snippet":"Бюджет согласован"}]
            }
        """.trimIndent(), MeetingContextDto::class.java)

        assertEquals("calendar-id", detail.meeting.id)
        assertEquals("invitation-id", detail.meeting.sourceEventId)
        assertEquals("result-id", detail.references.single().meetingResultId)
        assertEquals("transcript-id", detail.references.single().id)
        assertEquals("2026-09-14T09:00:00Z", detail.generatedAt)
    }

    @Test
    fun existingChatAndEmailReferencesRemainCompatible() {
        val reference = Gson().fromJson("""
            {"key":"E2", "kind":"event", "id":"email-id", "title":"Orion", "snippet":"Срок перенесли"}
        """.trimIndent(), ChatReferenceDto::class.java)

        assertNull(reference.meetingResultId)
        assertEquals("email-id", reference.id)
    }
}
