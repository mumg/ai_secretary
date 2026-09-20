package net.muratov.assistant

import net.muratov.assistant.i18n.tr

import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

internal fun formatMeetingResultPeriod(
    startsAt: String,
    endsAt: String,
    originType: String,
    calendarMeetingId: String?,
    zone: ZoneId = ZoneId.systemDefault(),
): String = runCatching {
    val start = OffsetDateTime.parse(startsAt).atZoneSameInstant(zone)
    val end = OffsetDateTime.parse(endsAt).atZoneSameInstant(zone)
    val locale = net.muratov.assistant.i18n.Language.locale
    val dateTime = net.muratov.assistant.i18n.dateTimeFormatter()
    val startText = start.format(dateTime)
    // Unlinked email results store the message timestamp in both interval fields.
    if (originType == "email_followup" && calendarMeetingId == null && start.isEqual(end)) {
        tr("Письмо получено {0} · время встречи неизвестно" , startText)
    } else if (!end.isAfter(start)) {
        tr("{0} · время окончания неизвестно" , startText)
    } else if (start.toLocalDate() != end.toLocalDate()) {
        "${startText} — ${end.format(dateTime)}"
    } else {
        val sameMinute = start.hour == end.hour && start.minute == end.minute
        val startFormat = if (sameMinute) {
            DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm:ss", locale)
        } else {
            dateTime
        }
        val endFormat = DateTimeFormatter.ofPattern(
            if (sameMinute) "HH:mm:ss" else "HH:mm",
            locale,
        )
        "${start.format(startFormat)} — ${end.format(endFormat)}"
    }
}.getOrDefault(tr("Время встречи неизвестно"))
