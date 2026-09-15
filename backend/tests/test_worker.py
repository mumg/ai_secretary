from datetime import UTC, datetime, timedelta
from unittest import TestCase

from improver.services.chat_queue import chat_retry_delay
from improver.services.pipeline import analysis_retry_delay
from improver.services.scheduling import next_worker_delay, source_sync_due
from improver.worker import source_error_message


class WorkerSchedulingTests(TestCase):
    def test_source_error_message_does_not_expose_exception_text(self) -> None:
        class AuthenticationError(Exception):
            pass

        secret = "sensitive-url-and-token"
        message = source_error_message(AuthenticationError(secret))

        self.assertEqual(message, "Ошибка авторизации источника")
        self.assertNotIn(secret, message)

    def test_non_empty_queue_is_processed_without_polling_pause(self) -> None:
        self.assertEqual(next_worker_delay(processed=10, poll_interval_seconds=60), 0)
        self.assertEqual(next_worker_delay(processed=1, poll_interval_seconds=60), 0)

    def test_empty_queue_uses_configured_polling_interval(self) -> None:
        self.assertEqual(next_worker_delay(processed=0, poll_interval_seconds=60), 60)

    def test_source_sync_respects_its_own_polling_interval(self) -> None:
        now = datetime(2026, 9, 14, 18, 30, tzinfo=UTC)
        self.assertFalse(source_sync_due(now - timedelta(minutes=14), now, 900))
        self.assertTrue(source_sync_due(now - timedelta(minutes=15), now, 900))
        self.assertTrue(source_sync_due(None, now, 900))

    def test_analysis_retry_uses_bounded_exponential_backoff(self) -> None:
        self.assertEqual(analysis_retry_delay(1).total_seconds(), 30)
        self.assertEqual(analysis_retry_delay(2).total_seconds(), 60)
        self.assertEqual(analysis_retry_delay(20).total_seconds(), 3_600)

    def test_chat_retry_uses_bounded_exponential_backoff(self) -> None:
        self.assertEqual(chat_retry_delay(1).total_seconds(), 30)
        self.assertEqual(chat_retry_delay(2).total_seconds(), 60)
        self.assertEqual(chat_retry_delay(20).total_seconds(), 3_600)
