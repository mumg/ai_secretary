from unittest import TestCase

from improver.config import IdentityConfig
from improver.enums import Direction
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

    def test_user_among_recipients_is_eligible_for_contextual_model_decision(self) -> None:
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

        self.assertFalse(without_name.sole_recipient)
        self.assertTrue(without_name.eligible)
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

    def test_outgoing_message_is_eligible_for_own_commitment(self) -> None:
        event = self.event(
            "Согласую перечень сервисов",
            [{"role": "to", "address": "colleague@example.test"}],
            author="Иван Петров <ivan@example.test>",
        )
        event.direction = Direction.OUTGOING

        signals = assignment_signals(event, self.identity)

        self.assertTrue(signals.user_is_author)
        self.assertTrue(signals.eligible)

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


class NamesakeAssignmentTests(TestCase):
    setUp = AssignmentSignalsTests.setUp
    event = AssignmentSignalsTests.event

    def participants(self):
        return [
            {"name": "Иван Петров", "address": "ivan@example.test", "role": "to"},
            {"name": "Иван Сидоров", "address": "sidorov@example.test", "role": "cc"},
        ]

    def test_foreign_mailbox_with_same_first_name_is_not_user(self):
        signals = assignment_signals(
            self.event("Иван, подготовь документ", [self.participants()[1]]), self.identity
        )
        self.assertFalse(signals.user_is_recipient)
        self.assertFalse(signals.eligible)

    def test_two_first_names_require_context_without_discarding_candidate(self):
        signals = assignment_signals(
            self.event("Иван, подготовь документ", self.participants()), self.identity
        )
        self.assertTrue(signals.eligible)
        self.assertTrue(signals.name_ambiguous)
        self.assertIn("иван", signals.ambiguous_names)
        self.assertIn("петров", signals.unambiguous_names)

    def test_unique_first_name_among_named_recipients_is_valid(self):
        people = [self.participants()[0], {"name": "Анна Сидорова", "address": "anna@example.test"}]
        signals = assignment_signals(self.event("Иван, подготовь документ", people), self.identity)
        self.assertTrue(signals.eligible)
        self.assertFalse(signals.name_ambiguous)

    def test_namesake_from_previous_messages_is_considered(self):
        signals = assignment_signals(
            self.event("Иван, подготовь документ", [self.participants()[0]]),
            self.identity,
            [{"participants": [self.participants()[1]], "body": "Обсуждение"}],
        )
        self.assertTrue(signals.name_ambiguous)

    def test_user_mailbox_aliases_do_not_create_namesakes(self):
        self.identity.addresses.append("ivan.work@example.test")
        people = [
            self.participants()[0],
            {"name": "Иван Петров", "address": "ivan.work@example.test"},
        ]
        signals = assignment_signals(self.event("Иван, подготовь документ", people), self.identity)
        self.assertTrue(signals.sole_recipient)
        self.assertFalse(signals.name_ambiguous)

    def test_same_full_name_with_different_addresses_is_ambiguous(self):
        people = self.participants()
        people[1]["name"] = "Иван Петров"
        signals = assignment_signals(
            self.event("Иван Петров, подготовь документ", people), self.identity
        )
        self.assertTrue(signals.name_ambiguous)
        self.assertFalse(signals.unambiguous_names)

    def test_identical_meeting_names_with_distinct_ids_are_not_merged(self):
        people = [
            {"name": "Иван Петров", "external_id": "one", "role": "speaker"},
            {"name": "Иван Петров", "external_id": "two", "role": "speaker"},
        ]
        signals = assignment_signals(self.event("Иван, подготовь документ", people), self.identity)
        self.assertTrue(signals.name_ambiguous)
        self.assertFalse(signals.sole_recipient)

    def test_unique_addressless_first_name_in_meeting_is_eligible(self):
        people = [{"name": "Иван", "role": "speaker"}, {"name": "Анна", "role": "speaker"}]
        signals = assignment_signals(self.event("Иван, подготовь документ", people), self.identity)
        self.assertTrue(signals.eligible)
        self.assertFalse(signals.name_ambiguous)

    def test_at_shared_name_is_still_ambiguous(self):
        signals = assignment_signals(
            self.event("@Иван, подготовь документ", self.participants()), self.identity
        )
        self.assertTrue(signals.directly_mentioned)
        self.assertTrue(signals.name_ambiguous)

    def test_namesake_author_is_part_of_context(self):
        signals = assignment_signals(
            self.event(
                "Иван, подготовь документ",
                [self.participants()[0]],
                author="Иван Сидоров <sidorov@example.test>",
            ),
            self.identity,
        )
        self.assertTrue(signals.name_ambiguous)
