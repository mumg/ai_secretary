from unittest import TestCase

from improver.config import IdentityConfig
from improver.models import CommunicationEvent
from improver.services.assignment import assignment_signals


class AssignmentSignalsTests(TestCase):
    def setUp(self) -> None:
        self.identity = IdentityConfig(
            names=["Иван Петров"],
            addresses=["ivan@example.test"],
        )

    def event(
        self,
        body: str,
        participants: list[dict[str, str]],
        author: str = "Sender <sender@example.test>",
    ) -> CommunicationEvent:
        return CommunicationEvent(body=body, author=author, participants=participants)

    def test_direct_at_mention_is_eligible_without_recipient_match(self) -> None:
        signals = assignment_signals(
            self.event(
                "@ivan, подготовь документ",
                [{"role": "to", "address": "team@example.test", "name": "Team"}],
            ),
            self.identity,
        )

        self.assertTrue(signals.directly_mentioned)
        self.assertTrue(signals.eligible)

    def test_user_as_only_recipient_is_eligible_without_name(self) -> None:
        signals = assignment_signals(
            self.event(
                "Подготовьте документ",
                [
                    {"role": "from", "address": "sender@example.test"},
                    {"role": "to", "address": "ivan@example.test"},
                ],
            ),
            self.identity,
        )

        self.assertTrue(signals.user_is_recipient)
        self.assertTrue(signals.sole_recipient)
        self.assertTrue(signals.eligible)

    def test_user_among_recipients_requires_name_or_surname(self) -> None:
        participants = [
            {"role": "to", "address": "ivan@example.test"},
            {"role": "cc", "address": "colleague@example.test"},
        ]

        without_name = assignment_signals(
            self.event("Подготовьте документ", participants), self.identity
        )
        by_first_name = assignment_signals(
            self.event("Иван, подготовь документ", participants), self.identity
        )
        by_surname = assignment_signals(
            self.event("Петров, подготовьте документ", participants), self.identity
        )

        self.assertFalse(without_name.eligible)
        self.assertTrue(by_first_name.eligible)
        self.assertTrue(by_surname.eligible)

    def test_other_only_recipient_is_not_eligible(self) -> None:
        signals = assignment_signals(
            self.event(
                "Подготовьте документ",
                [{"role": "to", "address": "colleague@example.test"}],
            ),
            self.identity,
        )

        self.assertFalse(signals.user_is_recipient)
        self.assertFalse(signals.eligible)

    def test_legacy_participants_exclude_author_from_recipient_count(self) -> None:
        signals = assignment_signals(
            self.event(
                "Подготовьте документ",
                [
                    {"address": "sender@example.test", "name": "Sender"},
                    {"address": "ivan@example.test", "name": "Иван Петров"},
                ],
            ),
            self.identity,
        )

        self.assertTrue(signals.sole_recipient)
        self.assertTrue(signals.eligible)
