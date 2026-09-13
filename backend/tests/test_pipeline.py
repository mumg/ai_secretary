from datetime import UTC, datetime
from unittest import TestCase

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from improver.models import CommunicationEvent
from improver.services.ollama import MailingSignal
from improver.services.pipeline import (
    apply_mailing_classification,
    missing_meeting_result_signal_condition,
)


class PipelineQueryTests(TestCase):
    def test_high_confidence_mailing_is_excluded_from_threads(self) -> None:
        event = CommunicationEvent(event_type="email")
        signal = MailingSignal(
            detected=True,
            confidence=0.91,
            kind="newsletter",
        )

        detected = apply_mailing_classification(
            event,
            signal,
            datetime.now(UTC),
        )

        self.assertTrue(detected)
        self.assertTrue(event.is_mailing)
        self.assertEqual(event.mailing_version, 1)

    def test_low_confidence_mailing_remains_visible(self) -> None:
        event = CommunicationEvent(event_type="email")
        signal = MailingSignal(
            detected=True,
            confidence=0.6,
            kind="uncertain",
        )

        detected = apply_mailing_classification(
            event,
            signal,
            datetime.now(UTC),
        )

        self.assertFalse(detected)
        self.assertFalse(event.is_mailing)

    def test_meeting_result_backfill_condition_compiles_for_postgres(self) -> None:
        statement = select(CommunicationEvent.id).where(
            missing_meeting_result_signal_condition()
        )

        sql = str(statement.compile(dialect=postgresql.dialect()))

        self.assertIn("EXISTS (SELECT", sql)
        self.assertIn("jsonb_exists", sql)
