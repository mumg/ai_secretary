from unittest import IsolatedAsyncioTestCase, TestCase

import httpx

from improver.config import AppConfig, SourceConfig
from improver.connectors.mts_link import (
    MtsLinkConnector,
    _data_items,
    _format_transcript,
    _participants,
)
from improver.services.mts_link import (
    find_mts_link_url_in_payload,
    mts_link_reference_keys,
    references_overlap,
)


class MtsLinkParsingTests(TestCase):
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
        calendar_keys = mts_link_reference_keys(
            "Подключение: https://my.mts-link.ru/j/123456789"
        )
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


class MtsLinkHttpTests(IsolatedAsyncioTestCase):
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
