"""Reindex existing email threads with the same rules used for newly received mail.

Run with --apply to commit; without it the transaction is rolled back.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import raiseload

from improver.models import CommunicationEvent
from improver.services.llm import OllamaAnalyzer
from improver.services.threads import index_email_subjects, reconcile_email_thread


async def reconcile_subject_archive(session, analyzer, now):
    count = await index_email_subjects(session)
    events = list(
        (
            await session.scalars(
                select(CommunicationEvent)
                .where(CommunicationEvent.event_type == "email")
                .order_by(CommunicationEvent.occurred_at, CommunicationEvent.id)
                .options(raiseload("*"))
            )
        ).all()
    )
    seen = set()
    methods = Counter()
    for event in events:
        identity = (event.source_id, event.subject_key)
        if not event.subject_key or identity in seen:
            continue
        seen.add(identity)
        await reconcile_email_thread(session, event, now, analyzer)
        methods[event.raw_headers["Subject-Thread-Match"]["method"]] += 1
    return {"emails": count, "subjects": len(seen), "methods": dict(methods)}


async def main(apply: bool):
    from improver.db import SessionFactory, engine
    from improver.services.settings import load_runtime_config

    try:
        async with SessionFactory() as session:
            config = await load_runtime_config(session)
            result = await reconcile_subject_archive(
                session,
                OllamaAnalyzer(config),
                datetime.now(UTC),
            )
            if apply:
                await session.commit()
            else:
                await session.rollback()
            print({**result, "committed": apply}, flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
