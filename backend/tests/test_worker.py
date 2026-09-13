from unittest import TestCase

from improver.services.pipeline import analysis_retry_delay
from improver.services.scheduling import next_worker_delay


class WorkerSchedulingTests(TestCase):
    def test_non_empty_queue_is_processed_without_polling_pause(self) -> None:
        self.assertEqual(next_worker_delay(processed=10, poll_interval_seconds=60), 0)
        self.assertEqual(next_worker_delay(processed=1, poll_interval_seconds=60), 0)

    def test_empty_queue_uses_configured_polling_interval(self) -> None:
        self.assertEqual(next_worker_delay(processed=0, poll_interval_seconds=60), 60)

    def test_analysis_retry_uses_bounded_exponential_backoff(self) -> None:
        self.assertEqual(analysis_retry_delay(1).total_seconds(), 30)
        self.assertEqual(analysis_retry_delay(2).total_seconds(), 60)
        self.assertEqual(analysis_retry_delay(20).total_seconds(), 3_600)
