from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime, time, timedelta

import httpx
from sqlalchemy import select

from improver.db import SessionFactory
from improver.models import CommunicationEvent, Meeting
from improver.services.calendar import BusinessCalendar
from improver.services.settings import load_runtime_config


async def main() -> None:
    async with SessionFactory() as session:
        config = await load_runtime_config(session)
        now = BusinessCalendar(config).now()
        start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        rows = list(
            (
                await session.execute(
                    select(Meeting, CommunicationEvent.body)
                    .join(CommunicationEvent, CommunicationEvent.id == Meeting.source_event_id)
                    .where(Meeting.starts_at < start + timedelta(days=1), Meeting.ends_at > start)
                )
            ).all()
        )

    adjustments: Counter[int] = Counter()
    inconclusive = 0
    async with httpx.AsyncClient(timeout=config.llm.request_timeout_seconds) as client:
        for meeting, body in rows:
            local_start = meeting.starts_at.astimezone(now.tzinfo)
            payload = {
                "model": config.llm.model,
                "stream": False,
                "think": False,
                "format": {
                    "type": "object",
                    "properties": {
                        "adjustment_hours": {"type": ["integer", "null"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["adjustment_hours", "confidence"],
                },
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Определи только поправку часового пояса. Сравни переданное "
                            "календарное локальное время начала с явно написанным в тексте "
                            "временем этой же встречи. adjustment_hours = текстовое время "
                            "минус календарное. Если в тексте нет однозначного времени, верни "
                            "null. Не возвращай никакие "
                            "фрагменты текста, имена, темы, даты или адреса."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "calendar_local_start": local_start.isoformat(),
                                "message_body": (body or "")[:20_000],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "options": {"temperature": 0, "num_ctx": 16_384, "num_predict": 128},
            }
            try:
                response = await client.post(f"{config.llm.base_url}/api/chat", json=payload)
                response.raise_for_status()
                result = json.loads(response.json()["message"]["content"])
                adjustment = result.get("adjustment_hours")
                confidence = float(result.get("confidence") or 0)
                if isinstance(adjustment, int) and -12 <= adjustment <= 12 and confidence >= 0.7:
                    adjustments[adjustment] += 1
                else:
                    inconclusive += 1
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                inconclusive += 1

    print(f"evaluated={len(rows)}")
    print("adjustments=" + json.dumps(dict(sorted(adjustments.items()))))
    print(f"inconclusive={inconclusive}")


if __name__ == "__main__":
    asyncio.run(main())
