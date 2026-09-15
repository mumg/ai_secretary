from unittest import TestCase

from improver.config import IdentityConfig
from improver.models import CommunicationEvent
from improver.services.identity_reconciliation import _currently_eligible, _previously_eligible


class IdentityReconciliationTests(TestCase):
    def test_missing_assignment_result_is_not_previously_eligible(self) -> None:
        self.assertFalse(_previously_eligible(CommunicationEvent(analysis_result=None)))

    def test_previous_assignment_eligibility_is_detected(self) -> None:
        event = CommunicationEvent(
            analysis_result={"assignment_signals": {"eligible": True}}
        )

        self.assertTrue(_previously_eligible(event))

    def test_recipient_email_is_eligible_even_when_only_name_is_quoted(self) -> None:
        event = CommunicationEvent(
            event_type="email",
            body="Информация к сведению.\n\nFrom: Sender\nИван, подготовьте документ",
            author="sender@example.test",
            participants=[
                {"role": "to", "address": "ivan@example.test"},
                {"role": "cc", "address": "other@example.test"},
            ],
        )
        identity = IdentityConfig(
            names=["Иван Петров"], addresses=["ivan@example.test"]
        )

        self.assertTrue(_currently_eligible(event, identity))
