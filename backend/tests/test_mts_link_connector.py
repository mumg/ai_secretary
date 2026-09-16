from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

import httpx

from improver.config import AppConfig, SourceConfig
from improver.models import MeetingResult
from improver.connectors.mts_link import (
    MtsLinkConnector,
    _data_items,
    _format_transcript,
    _meeting_interval,
    _published_transcript_interval,
    _participants,
)
from improver.services.mts_link import (
    find_mts_link_url_in_payload,
    mts_link_reference_keys,
    references_overlap,
    mts_link_join_url,
)


class MtsLinkParsingTests(TestCase):
    def test_join_url_uses_room_path_and_ignores_image_urls(self) -> None:
        self.assertEqual(mts_link_join_url("https://mts.mts-link.ru/j/MTC/700001/session/800002"),
                         "https://mts.mts-link.ru/j/MTC/700001")
        self.assertIsNone(find_mts_link_url_in_payload({"image": "https://my.mts-link.ru/images/event-default.png"}))

    def test_published_occurrence_uses_speech_span_without_room_end(self) -> None:
        session = {"activitySessionId": "occurrence", "endsAt": None}
        state = {"activitySessionId": "occurrence", "isPublished": True}
        utterances = [{"dateTime": "2026-09-15T16:58:15+03:00"},
                      {"dateTime": "2026-09-15T16:00:29+03:00"}]
        self.assertEqual(_published_transcript_interval(session, state, utterances), (
            datetime(2026, 9, 15, 13, 0, 29, tzinfo=UTC),
            datetime(2026, 9, 15, 13, 58, 15, tzinfo=UTC),
        ))
        for changed in [{**state, "activitySessionId": "other"},
                        {**state, "isPublished": False}, {**state, "isDisabled": True}]:
            self.assertIsNone(_published_transcript_interval(session, changed, utterances))
        self.assertIsNone(_published_transcript_interval(session, state, utterances[:1]))

    def test_meeting_times_come_from_actual_api_session_interval(self) -> None:
        interval = _meeting_interval({
            "startsAt": "2026-09-14T16:30:01+03:00",
            "endsAt": "2026-09-14T16:50:15+03:00",
            "estimatedAt": "2026-09-10T09:00:00+03:00",
        })
        self.assertEqual(interval, (
            datetime(2026, 9, 14, 13, 30, 1, tzinfo=UTC),
            datetime(2026, 9, 14, 13, 50, 15, tzinfo=UTC),
        ))

    def test_missing_or_invalid_bounds_do_not_invent_meeting_times(self) -> None:
        for fields in [
            {"startsAt": "2026-09-10T09:57:52+03:00", "endsAt": None,
             "estimatedAt": "2026-09-14T17:00:00+03:00"},
            {"startsAt": "2026-09-14T09:00:00Z", "endsAt": "2026-09-14T09:00:00Z"},
            {"startsAt": "2026-09-14T10:00:00Z", "endsAt": "2026-09-14T09:00:00Z"},
            {"startsAt": "invalid", "endsAt": "2026-09-14T10:00:00Z"},
            {"startsAt": None, "endsAt": "2026-09-14T10:00:00Z"},
            {"startsAt": "2026-09-14T09:00:00", "endsAt": "2026-09-14T10:00:00"},
        ]:
            with self.subTest(fields=fields):
                self.assertIsNone(_meeting_interval(fields))

    def test_structured_utterances_are_formatted_without_external_summary(self) -> None:
        payload = {
            "data": {
                "transcriptName": "Синтетическая встреча",
                "summary": "Это резюме МТС не должно попасть в текст",
                "items": [
                    {
                        "id": 1,
                        "userId": 10,
                        "nickname": "Иван",
                        "dateTime": "2026-09-12T09:30:00+03:00",
                        "text": "Обсудили следующий шаг.",
                    }
                ],
            }
        }

        body = _format_transcript(_data_items(payload))

        self.assertIn("Иван", body)
        self.assertIn("Обсудили следующий шаг.", body)
        self.assertNotIn("резюме МТС", body)

    def test_participants_are_unique(self) -> None:
        participants = _participants(
            [
                {"userId": 10, "nickname": "Иван"},
                {"userId": 10, "nickname": "Иван П."},
                {"userId": 11, "nickname": "Анна"},
            ]
        )

        self.assertEqual([item["name"] for item in participants], ["Иван", "Анна"])

    def test_calendar_and_session_references_match_by_url_identifier(self) -> None:
        calendar_keys = mts_link_reference_keys("Подключение: https://my.mts-link.ru/j/123456789")
        result_keys = mts_link_reference_keys(None, known_ids=(123456789, 777))

        self.assertTrue(references_overlap(calendar_keys, result_keys))

    def test_url_is_found_in_nested_api_payload(self) -> None:
        payload = {
            "eventSession": {"id": 1},
            "link": "https://my.mts-link.ru/j/123456789",
        }

        self.assertEqual(
            find_mts_link_url_in_payload(payload),
            "https://my.mts-link.ru/j/123456789",
        )

    def test_owner_and_route_segments_do_not_merge_different_meetings(self) -> None:
        for left, right in [
            ("/123456/700001", "/123456/700002"),
            ("/j/123456/700001", "/j/123456/700002"),
            ("/events/700001", "/events/700002"),
        ]:
            with self.subTest(left=left):
                self.assertFalse(
                    references_overlap(
                        mts_link_reference_keys("https://my.mts-link.ru" + left),
                        mts_link_reference_keys("https://my.mts-link.ru" + right),
                    )
                )

    def test_account_query_and_generic_pages_are_not_meeting_ids(self) -> None:
        self.assertEqual(mts_link_reference_keys("https://my.mts-link.ru/login?userId=1234"), [])
        self.assertFalse(
            references_overlap(
                mts_link_reference_keys("https://my.mts-link.ru/j/700001?userId=1234"),
                mts_link_reference_keys("https://my.mts-link.ru/j/700002?userId=1234"),
            )
        )

    def test_meeting_identifier_survives_owner_path(self) -> None:
        self.assertTrue(
            references_overlap(
                mts_link_reference_keys("https://my.mts-link.ru/123456/700001"),
                mts_link_reference_keys(known_ids=(700001,)),
            )
        )


class MtsLinkHttpTests(IsolatedAsyncioTestCase):
    async def test_persist_published_recurring_transcript_without_room_end(self) -> None:
        connector = MtsLinkConnector(SourceConfig(
            id="mts", type="mts_link", base_url="https://gw.mts-link.ru",
            credential="synthetic-token"), AppConfig())
        session = Mock(scalar=AsyncMock(return_value=None), flush=AsyncMock())
        with (
            patch("improver.connectors.mts_link.link_result_to_calendar", new=AsyncMock()),
            patch("improver.connectors.mts_link.attach_email_results_to_transcript", new=AsyncMock()),
        ):
            created = await connector._persist(session,
                {"id": "room", "activitySessionId": "past-occurrence", "endsAt": None},
                {"transcriptId": "42", "activitySessionId": "past-occurrence", "isPublished": True},
                {"transcriptName": "Recurring meeting"},
                [{"text": "First", "dateTime": "2026-09-15T16:00:00+03:00"},
                 {"text": "Last", "dateTime": "2026-09-15T16:58:00+03:00"}])
        self.assertTrue(created)
        event = session.add.call_args_list[0].args[0]
        result = session.add.call_args_list[1].args[0]
        self.assertEqual(event.raw_headers["MTS-Link"]["time_basis"], "transcript")
        self.assertEqual(result.activity_session_id, "past-occurrence")
        self.assertEqual(result.ends_at, datetime(2026, 9, 15, 13, 58, tzinfo=UTC))

    async def test_persist_defers_transcript_until_api_has_actual_end(self) -> None:
        connector = MtsLinkConnector(
            SourceConfig(
                id="mts", type="mts_link", base_url="https://gw.mts-link.ru",
                credential="synthetic-token",
            ),
            AppConfig(),
        )
        session = Mock(scalar=AsyncMock(return_value=None), flush=AsyncMock())
        result = await connector._persist(
            session,
            {"id": "room", "startsAt": "2026-09-10T09:57:52+03:00",
             "endsAt": None, "estimatedAt": "2026-09-14T17:00:00+03:00"},
            {"transcriptId": "42"}, {},
            [{"text": "Обсудили вопросы", "dateTime": "2026-09-14T17:05:00+03:00"}],
        )
        self.assertFalse(result)
        session.add.assert_not_called()

    async def test_persist_keeps_api_times_independent_of_transcript_creation(self) -> None:
        connector = MtsLinkConnector(
            SourceConfig(
                id="mts", type="mts_link", base_url="https://gw.mts-link.ru",
                credential="synthetic-token",
            ),
            AppConfig(),
        )
        session = Mock(scalar=AsyncMock(return_value=None), flush=AsyncMock())
        with (
            patch("improver.connectors.mts_link.link_result_to_calendar", new=AsyncMock()),
            patch("improver.connectors.mts_link.attach_email_results_to_transcript", new=AsyncMock()),
        ):
            created = await connector._persist(
                session,
                {"id": "room", "activitySessionId": "activity",
                 "startsAt": "2026-09-14T17:00:00+03:00",
                 "endsAt": "2026-09-14T18:00:00+03:00"},
                {"transcriptId": "42"},
                {"createdAt": "2026-09-14T18:37:37+03:00"},
                [{"text": "Обсудили вопросы", "dateTime": "2026-09-14T17:05:00+03:00"}],
            )
        self.assertTrue(created)
        result = next(call.args[0] for call in session.add.call_args_list
                      if isinstance(call.args[0], MeetingResult))
        self.assertEqual(result.starts_at, datetime(2026, 9, 14, 14, tzinfo=UTC))
        self.assertEqual(result.ends_at, datetime(2026, 9, 14, 15, tzinfo=UTC))

    async def test_download_uses_only_details_and_structured_utterances(self) -> None:
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            if request.url.path.endswith("/details"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "transcriptId": 42,
                            "transcriptName": "Тест",
                            "ownerName": "Владелец",
                            "createdAt": "2026-09-12T09:00:00+03:00",
                            "visibility": "private",
                            "organization": {"id": 1, "name": "Тест"},
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "transcriptName": "Тест",
                        "items": [
                            {
                                "id": 1,
                                "nickname": "Участник",
                                "text": "Исходная реплика",
                                "dateTime": "2026-09-12T09:00:00+03:00",
                            }
                        ],
                    }
                },
            )

        connector = MtsLinkConnector(
            SourceConfig(
                id="mts",
                type="mts_link",
                base_url="https://gw.mts-link.ru",
                credential="synthetic-token",
            ),
            AppConfig(),
        )
        async with httpx.AsyncClient(
            base_url="https://gw.mts-link.ru",
            transport=httpx.MockTransport(handler),
        ) as client:
            details, utterances = await connector._download_transcript(client, "42")

        self.assertEqual(details["transcriptName"], "Тест")
        self.assertEqual(utterances[0]["text"], "Исходная реплика")
        self.assertEqual(
            requested_paths,
            ["/api/transcript/42/details", "/api/transcript/42"],
        )

    async def test_download_uses_gateway_supported_page_size(self) -> None:
        requested_per_page: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if not request.url.path.endswith("/details"):
                requested_per_page.append(request.url.params.get("perPage"))
            return httpx.Response(200, json={"data": {"items": []}})

        connector = MtsLinkConnector(
            SourceConfig(
                id="mts",
                type="mts_link",
                base_url="https://gw.mts-link.ru",
                credential="synthetic-token",
            ),
            AppConfig(),
        )
        async with httpx.AsyncClient(
            base_url="https://gw.mts-link.ru",
            transport=httpx.MockTransport(handler),
        ) as client:
            await connector._download_transcript(client, "42")

        self.assertEqual(requested_per_page, ["10000"])
