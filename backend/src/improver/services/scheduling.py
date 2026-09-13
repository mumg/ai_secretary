def next_worker_delay(processed: int, poll_interval_seconds: int) -> int:
    return 0 if processed else poll_interval_seconds
