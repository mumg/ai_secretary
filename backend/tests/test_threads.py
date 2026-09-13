from datetime import UTC, datetime
from unittest import TestCase

from improver.models import CommunicationEvent
from improver.services.threads import merge_participants, thread_key, thread_title


def event(**values: object) -> CommunicationEvent:
    defaults = {
        "source_id": "mail",
        "source_type": "imap",
        "external_id": "<message@example.com>",
        "event_type": "email",
        "occurred_at": datetime(2026, 9, 11, tzinfo=UTC),
        "body": "Текст",
        "content_hash": "hash",
    }
    defaults.update(values)
    return CommunicationEvent(**defaults)


class ConversationThreadTests(TestCase):
    def test_message_without_thread_uses_its_external_id(self) -> None:
        self.assertEqual(thread_key(event()), "<message@example.com>")

    def test_reply_prefixes_are_removed_from_title(self) -> None:
        self.assertEqual(thread_title(event(subject="Re: FW: Статус проекта")), "Статус проекта")

    def test_participants_are_merged_by_address(self) -> None:
        merged = merge_participants(
            [{"name": "Иван", "address": "ivan@example.com"}],
            [
                {"name": "Иван П.", "address": "IVAN@example.com"},
                {"name": "Анна", "address": "anna@example.com"},
            ],
        )

        self.assertEqual(len(merged), 2)
