from datetime import datetime


def next_worker_delay(processed: int, poll_interval_seconds: int) -> int:
    return 0 if processed else poll_interval_seconds


def source_sync_due(
    last_attempt_at: datetime | None,
    now: datetime,
    poll_interval_seconds: int,
) -> bool:
    if last_attempt_at is None:
        return True
    return (now - last_attempt_at).total_seconds() >= poll_interval_seconds
