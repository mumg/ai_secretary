package net.muratov.assistant

import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Test

class MeetingResultTimeTest {
    private val moscow = ZoneId.of("Europe/Moscow")

    @Test
    fun standaloneEmailShowsReceiptTimeInsteadOfInventedMeetingInterval() {
        assertEquals(
            "Письмо получено 14.09.2026 18:37 · время встречи неизвестно",
            formatMeetingResultPeriod(
                "2026-09-14T15:37:37Z", "2026-09-14T18:37:37+03:00",
                "email_followup", null, moscow,
            ),
        )
    }

    @Test
    fun linkedEmailAndTranscriptKeepActualIntervals() {
        for ((origin, calendarId) in listOf(
            "email_followup" to "calendar-id",
            "mts_transcript" to null,
        )) {
            assertEquals(
                "14.09.2026 12:00 — 13:30",
                formatMeetingResultPeriod(
                    "2026-09-14T09:00:00Z", "2026-09-14T10:30:00Z",
                    origin, calendarId, moscow,
                ),
            )
        }
    }

    @Test
    fun absentTranscriptEndIsExplicitlyUnknown() {
        assertEquals(
            "14.09.2026 12:00 · время окончания неизвестно",
            formatMeetingResultPeriod(
                "2026-09-14T09:00:00Z", "2026-09-14T09:00:00Z",
                "mts_transcript", null, moscow,
            ),
        )
    }

    @Test
    fun differentDaysShowBothDatesEvenWhenClockTimesMatch() {
        assertEquals(
            "14.09.2026 12:00 — 15.09.2026 12:00",
            formatMeetingResultPeriod(
                "2026-09-14T09:00:00Z", "2026-09-15T09:00:00Z",
                "mts_transcript", null, moscow,
            ),
        )
    }

    @Test
    fun shortMeetingDoesNotRoundToIdenticalTimes() {
        assertEquals(
            "14.09.2026 12:00:03 — 12:00:49",
            formatMeetingResultPeriod(
                "2026-09-14T09:00:03Z", "2026-09-14T09:00:49Z",
                "mts_transcript", null, moscow,
            ),
        )
    }

    @Test
    fun malformedDatesDoNotExposeAnUnusableInterval() {
        assertEquals(
            "Время встречи неизвестно",
            formatMeetingResultPeriod("invalid", "invalid", "mts_transcript", null, moscow),
        )
    }
}
