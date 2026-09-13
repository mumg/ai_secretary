import base64
from unittest import TestCase

from improver.connectors.imap import _parse_message, _root_thread_index
from improver.services.text import clean_email_body


class ImapParserTests(TestCase):
    def test_decodes_body_participants_and_attachment(self) -> None:
        raw = (
            b"From: Sender <sender@example.com>\r\n"
            b"To: User <user@example.com>\r\n"
            b"Subject: =?utf-8?b?0KLQtdGB0YI=?=\r\n"
            b"Message-ID: <message-1@example.com>\r\n"
            b"Date: Thu, 10 Sep 2026 09:00:00 +0300\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=part\r\n\r\n"
            b"--part\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Please prepare the report.\r\n"
            b"--part\r\nContent-Type: application/pdf\r\n"
            b"Content-Disposition: attachment; filename=report.pdf\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\nJVBERg==\r\n"
            b"--part--\r\n"
        )
        parsed = _parse_message(42, raw)
        self.assertEqual(parsed.external_id, "<message-1@example.com>")
        self.assertEqual(parsed.subject, "Тест")
        self.assertIn("Please prepare", parsed.body)
        self.assertEqual(parsed.attachments[0].filename, "report.pdf")
        self.assertEqual(parsed.participants[0]["role"], "from")
        self.assertEqual(parsed.participants[1]["address"], "user@example.com")
        self.assertEqual(parsed.participants[1]["role"], "to")

    def test_removes_quoted_reply_and_signature(self) -> None:
        body = "Новый ответ\n\n> старый текст\n--\nПодпись"
        self.assertEqual(clean_email_body(body), "Новый ответ")

    def test_outlook_thread_index_children_share_root(self) -> None:
        root_bytes = b"0123456789012345678901"
        root = base64.b64encode(root_bytes).decode("ascii")
        child = base64.b64encode(root_bytes + b"child").decode("ascii")

        self.assertEqual(_root_thread_index(child), _root_thread_index(root))

    def test_preserves_automatic_reply_and_calendar_markers(self) -> None:
        raw = (
            b"From: Calendar <calendar@example.com>\r\n"
            b"To: User <user@example.com>\r\n"
            b"Subject: Meeting response\r\n"
            b"Message-ID: <reply@example.com>\r\n"
            b"Auto-Submitted: auto-replied\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/calendar; method=REPLY; charset=utf-8\r\n\r\n"
            b"METHOD:REPLY\r\n"
        )

        parsed = _parse_message(1, raw)

        self.assertEqual(parsed.headers["Auto-Submitted"], "auto-replied")
        self.assertEqual(parsed.headers["Calendar-Method"], "REPLY")
        self.assertEqual(parsed.event_type, "email")

    def test_classifies_calendar_request_as_meeting_invitation(self) -> None:
        raw = (
            b"From: Organizer <organizer@example.test>\r\n"
            b"To: User <user@example.test>\r\n"
            b"Subject: Meeting\r\n"
            b"Message-ID: <meeting@example.test>\r\n"
            b"Date: Fri, 11 Sep 2026 08:00:00 +0300\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/calendar; method=REQUEST; charset=utf-8\r\n\r\n"
            b"BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
            b"UID:meeting-1@example.test\r\nDTSTART:20260911T100000Z\r\n"
            b"DTEND:20260911T110000Z\r\nSUMMARY:Meeting\r\n"
            b"END:VEVENT\r\nEND:VCALENDAR\r\n"
        )

        parsed = _parse_message(1, raw)

        self.assertEqual(parsed.event_type, "meeting_invitation")
        self.assertIn("Calendar-Event", parsed.headers)
