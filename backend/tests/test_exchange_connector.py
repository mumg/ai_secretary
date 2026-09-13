from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
from types import SimpleNamespace
from unittest import TestCase

from exchangelib import EWSDateTime

from improver.config import AppConfig, SourceConfig
from improver.connectors.exchange import ExchangeConnector
from improver.enums import Direction


class _Query:
    def __init__(self, items: object | list[object]):
        self.items = items if isinstance(items, list) else [items]
        self.only_fields: tuple[str, ...] = ()

    def only(self, *fields: str) -> _Query:
        self.only_fields = fields
        return self

    def order_by(self, _field: str) -> _Query:
        return self

    def __iter__(self):
        yield from self.items


class _Folder:
    def __init__(self, item: object):
        self.item = item
        self.filter_kwargs: dict[str, object] = {}
        self.query: _Query | None = None

    def filter(self, **kwargs: object) -> _Query:
        self.filter_kwargs = kwargs
        self.query = _Query(self.item)
        return self.query


class _CalendarFolder:
    def __init__(self, items: list[object]):
        self.items = items
        self.view_calls: list[dict[str, object]] = []
        self.query: _Query | None = None

    def view(self, **kwargs: object) -> _Query:
        self.view_calls.append(kwargs)
        self.query = _Query(self.items)
        return self.query


class ExchangeConnectorTests(TestCase):
    def test_fetch_folder_uses_queryset_iteration_supported_by_exchangelib_5(self) -> None:
        mime_message = EmailMessage()
        mime_message["From"] = "sender@example.test"
        mime_message["To"] = "recipient@example.test"
        mime_message["Subject"] = "Test"
        mime_message["Message-ID"] = "<message-id@example.test>"
        mime_message.set_content("Message body")
        received_at = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
        item = SimpleNamespace(
            id="exchange-item-id",
            mime_content=mime_message.as_bytes(),
            conversation_id=SimpleNamespace(id="conversation-id"),
            datetime_received=EWSDateTime.from_datetime(received_at),
        )
        inbox = _Folder(item)
        account = SimpleNamespace(inbox=inbox)
        source = SourceConfig(
            id="exchange",
            type="exchange",
            ews_url="https://exchange.example.test/EWS/Exchange.asmx",
            primary_smtp_address="recipient@example.test",
            username="recipient@example.test",
            credential="secret",
        )
        connector = ExchangeConnector(source, AppConfig())
        connector._account = lambda: account  # type: ignore[method-assign]

        messages = connector._fetch_folder("inbox", Direction.INCOMING, received_at)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].external_id, "<message-id@example.test>")
        self.assertEqual(messages[0].thread_id, "conversation-id")
        self.assertEqual(messages[0].occurred_at, received_at)
        self.assertIsInstance(inbox.filter_kwargs["datetime_received__gte"], EWSDateTime)
        self.assertEqual(
            inbox.query.only_fields,
            ("mime_content", "conversation_id", "datetime_received"),
        )

    def test_calendar_view_expands_recurring_meetings_into_unique_occurrences(self) -> None:
        first_start = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
        second_start = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)

        def occurrence(start: datetime) -> SimpleNamespace:
            return SimpleNamespace(
                id=f"item-{start.date().isoformat()}",
                uid="recurring-series@example.test",
                subject="Weekly meeting",
                start=EWSDateTime.from_datetime(start),
                end=EWSDateTime.from_datetime(start.replace(hour=9)),
                original_start=EWSDateTime.from_datetime(start),
                recurrence_id=None,
                is_all_day=False,
                is_cancelled=False,
                is_recurring=True,
                type="Occurrence",
                location="https://example.test/meeting",
                organizer=SimpleNamespace(
                    name="Organizer", email_address="organizer@example.test"
                ),
                required_attendees=[],
                optional_attendees=[],
                resources=[],
                text_body="Agenda",
                body=None,
                meeting_workspace_url=None,
                net_show_url=None,
                last_modified_time=EWSDateTime.from_datetime(first_start),
            )

        calendar = _CalendarFolder([occurrence(first_start), occurrence(second_start)])
        account = SimpleNamespace(calendar=calendar)
        source = SourceConfig(
            id="exchange",
            type="exchange",
            ews_url="https://exchange.example.test/EWS/Exchange.asmx",
            primary_smtp_address="recipient@example.test",
            username="recipient@example.test",
            credential="secret",
        )
        connector = ExchangeConnector(
            source,
            AppConfig(server={"timezone": "UTC", "public_url": "https://localhost"}),
        )
        connector._account = lambda: account  # type: ignore[method-assign]

        meetings = connector._fetch_calendar(
            datetime(2026, 9, 1, tzinfo=UTC),
            datetime(2026, 10, 1, tzinfo=UTC),
        )

        self.assertEqual(len(meetings), 2)
        self.assertEqual(meetings[0].series_uid, meetings[1].series_uid)
        self.assertNotEqual(meetings[0].occurrence_uid, meetings[1].occurrence_uid)
        self.assertNotEqual(meetings[0].external_id, meetings[1].external_id)
        self.assertTrue(meetings[0].recurring)
        self.assertIsInstance(calendar.view_calls[0]["start"], EWSDateTime)
        self.assertIsInstance(calendar.view_calls[0]["end"], EWSDateTime)
        self.assertEqual(calendar.view_calls[0]["max_items"], 500)
        self.assertIn("original_start", calendar.query.only_fields)

    def test_calendar_view_omits_meeting_organized_only_for_owner(self) -> None:
        starts_at = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
        item = SimpleNamespace(
            id="private-block",
            uid="private-block@example.test",
            subject="Private block",
            start=EWSDateTime.from_datetime(starts_at),
            end=EWSDateTime.from_datetime(starts_at.replace(hour=9)),
            original_start=None,
            recurrence_id=None,
            is_all_day=False,
            is_cancelled=False,
            is_recurring=False,
            type="Single",
            location=None,
            organizer=SimpleNamespace(
                name="Owner", email_address="recipient@example.test"
            ),
            required_attendees=[],
            optional_attendees=[],
            resources=[],
            text_body="",
            body=None,
            meeting_workspace_url=None,
            net_show_url=None,
            last_modified_time=EWSDateTime.from_datetime(starts_at),
        )
        calendar = _CalendarFolder([item])
        source = SourceConfig(
            id="exchange",
            type="exchange",
            ews_url="https://exchange.example.test/EWS/Exchange.asmx",
            primary_smtp_address="recipient@example.test",
            username="EXAMPLE\\recipient",
            credential="secret",
        )
        connector = ExchangeConnector(
            source,
            AppConfig(server={"timezone": "UTC", "public_url": "https://localhost"}),
        )
        connector._account = lambda: SimpleNamespace(calendar=calendar)  # type: ignore[method-assign]

        meetings = connector._fetch_calendar(
            datetime(2026, 9, 1, tzinfo=UTC),
            datetime(2026, 10, 1, tzinfo=UTC),
        )

        self.assertEqual(meetings, [])

    def test_calendar_view_keeps_ews_instant_without_local_time_reinterpretation(self) -> None:
        starts_at = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
        connector = ExchangeConnector(
            SourceConfig(
                id="exchange",
                type="exchange",
                ews_url="https://exchange.example.test/EWS/Exchange.asmx",
                primary_smtp_address="recipient@example.test",
                username="recipient@example.test",
                credential="secret",
            ),
            AppConfig(
                server={
                    "timezone": "Europe/Moscow",
                    "public_url": "https://localhost",
                }
            ),
        )

        parsed = connector._calendar_datetime(EWSDateTime.from_datetime(starts_at))

        self.assertEqual(parsed, starts_at)
        self.assertEqual(parsed.astimezone().timestamp(), starts_at.timestamp())
