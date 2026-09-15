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


class ExplicitItemAssignmentTests(TestCase):
    def setUp(self):
        self.identity = IdentityConfig(
            names=["Иван Петров", "Иван"], addresses=["ivan@example.test"]
        )
        self.event = CommunicationEvent(
            event_type="email",
            author="manager@example.test",
            participants=[{"name": "Иван Петров", "address": "ivan@example.test"}],
        )

    def verdict(self, body, evidence=None, context=None):
        from improver.services.assignment import task_assignment_verdict

        self.event.body = body
        return task_assignment_verdict(evidence or body, self.identity, self.event, context)

    def test_foreign_surname_and_initial(self):
        self.assertEqual(self.verdict("Назначить встречу — отв. Сидоров А."), "other")

    def test_user_surname_and_initial(self):
        self.assertEqual(self.verdict("Назначить встречу — отв. Петров И."), "user")

    def test_same_surname_different_initial(self):
        self.assertEqual(self.verdict("Назначить встречу — отв. Петров А."), "other")

    def test_foreign_mailbox_overrides_identical_full_name(self):
        self.assertEqual(
            self.verdict("Назначить встречу — отв. @Иван Петров<mailto:other@example.test>"),
            "other",
        )

    def test_user_mailbox_identifies_owner(self):
        self.assertEqual(
            self.verdict(
                "Назначить встречу — отв. @Петров Иван<mailto:ivan@example.test>, срок - 27.09."
            ),
            "user",
        )

    def test_unique_first_name(self):
        self.assertEqual(self.verdict("Подготовить документ — отв. Иван"), "user")

    def test_shared_first_name(self):
        self.event.participants.append({"name": "Иван Сидоров", "address": "other@example.test"})
        self.assertEqual(self.verdict("Подготовить документ — отв. Иван"), "uncertain")

    def test_shared_surname_and_initial(self):
        self.event.participants.append({"name": "Илья Петров", "address": "other@example.test"})
        self.assertEqual(self.verdict("Подготовить документ — отв. Петров И."), "uncertain")

    def test_previous_participants_resolve_namesakes(self):
        context = [
            {
                "occurred_at": "2026-09-14",
                "participants": [{"name": "Илья Петров", "address": "other@example.test"}],
            }
        ]
        self.assertEqual(
            self.verdict("Подготовить документ — отв. Петров И.", context=context), "uncertain"
        )

    def test_joint_responsibility_includes_user(self):
        for separator in (", ", " и ", " / "):
            with self.subTest(separator=separator):
                self.assertEqual(
                    self.verdict(
                        "Подготовить документ — отв. Сидоров А." + separator + "Петров И."
                    ),
                    "user",
                )

    def test_neighbouring_user_item_does_not_own_foreign_item(self):
        body = "1. Подготовить договор — отв. Сидоров А.\n2. Проверить смету — отв. Петров И."
        self.assertEqual(self.verdict(body, "Подготовить договор"), "other")
        self.assertEqual(self.verdict(body, "Проверить смету"), "user")

    def test_owner_on_next_line_or_paragraph(self):
        for separator in ("\n", "\n\n"):
            with self.subTest(separator=separator):
                body = "Подготовить договор" + separator + "- отв. Сидоров А."
                self.assertEqual(self.verdict(body, "Подготовить договор"), "other")

    def test_short_quote_recovers_omitted_owner(self):
        self.assertEqual(
            self.verdict("Подготовить договор — ответственный: Сидоров А.", "Подготовить договор"),
            "other",
        )

    def test_quote_spanning_different_items_cannot_borrow_user_assignment(self):
        body = "1. Подготовить договор — отв. Сидоров А.\n2. Проверить смету — отв. Петров И."
        self.assertEqual(self.verdict(body), "uncertain")

    def test_invented_quote_with_labelled_source_cannot_auto_assign(self):
        self.assertEqual(
            self.verdict(
                "Подготовить договор — отв. Сидоров А.", "Подготовить документы — отв. Петров И."
            ),
            "uncertain",
        )

    def test_team_owner_is_not_a_person(self):
        self.assertEqual(self.verdict("Подготовить договор — отв. ИМ"), "uncertain")

    def test_contextual_assignment_without_explicit_label_is_preserved(self):
        self.assertIsNone(self.verdict("Иван, подготовьте договор"))

    def test_old_quoted_instruction_does_not_create_task(self):
        old = "Подготовить договор — отв. Петров И."
        self.assertEqual(
            self.verdict("Спасибо, всё готово.\nFrom: old@example.test\n" + old, old), "stale"
        )

    def test_previous_task_without_current_instruction_is_not_recreated(self):
        old = "Подготовить договор — отв. Петров И."
        self.assertEqual(
            self.verdict(
                "Спасибо, всё готово.", old, context=[{"occurred_at": "2026-09-14", "body": old}]
            ),
            "stale",
        )

    def test_current_renewal_can_use_previous_context(self):
        old = "Подготовить договор — отв. Петров И."
        self.assertEqual(
            self.verdict(
                "Повторно подготовить договор — отв. Петров И.",
                context=[{"occurred_at": "2026-09-14", "body": old}],
            ),
            "user",
        )

    def test_two_initials_can_disambiguate_person(self):
        self.identity.names = ["Иван Сергеевич Петров"]
        self.assertEqual(self.verdict("Подготовить документ — отв. Петров И.С."), "user")
        self.assertEqual(self.verdict("Подготовить документ — отв. Петров И.А."), "other")

    def test_multiple_labels_in_single_paragraph_do_not_merge_owners(self):
        self.assertEqual(
            self.verdict(
                "Подготовить договор — отв. Сидоров А.; проверить смету — отв. Петров И.",
                "Подготовить договор",
            ),
            "uncertain",
        )

    def test_different_speaker_ids_with_identical_names_remain_ambiguous(self):
        self.event.participants = [
            {"name": "Иван Петров", "external_id": "one"},
            {"name": "Иван Петров", "external_id": "two"},
        ]
        self.assertEqual(self.verdict("Подготовить документ — отв. Петров И."), "uncertain")

    def test_markdown_owner_label(self):
        self.assertEqual(self.verdict("Подготовить договор — **отв.** Сидоров А."), "other")

    def test_missing_patronymic_in_profile_is_not_proof_of_foreign_owner(self):
        self.assertEqual(self.verdict("Подготовить договор — отв. Петров И.С."), "uncertain")
        self.assertEqual(
            self.verdict("Подготовить договор — отв. Петров Иван Сергеевич"), "uncertain"
        )
        self.assertEqual(self.verdict("Подготовить договор — отв. Петров А.С."), "other")

    def test_joint_owners_on_wrapped_line(self):
        self.assertEqual(self.verdict("Подготовить договор — отв. Сидоров А.,\nПетров И."), "user")

    def test_outlook_unicode_bullets_keep_owners_separate(self):
        body = " ⁃ Подготовить договор — отв. Сидоров А.\r\n ⁃ Проверить смету — отв. Петров И."
        self.assertEqual(self.verdict(body, "Подготовить договор"), "other")
        self.assertEqual(self.verdict(body, "Проверить смету"), "user")
