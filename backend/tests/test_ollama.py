from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

import httpx
from pydantic import ValidationError

from improver.services.ollama import (
    OLLAMA_UNSUPPORTED_SCHEMA_KEYS,
    AnalysisResult,
    ArchiveAnalysis,
    FormalizedTask,
    GroundedChatAnswer,
    MailingBatchResult,
    MeetingTopicMatch,
    OllamaAnalyzer,
    RelevantReferenceSelection,
    ResolvedDue,
    SemanticAnalysis,
    TaskExtractionResult,
    _retryable_ollama_error,
    is_ollama_processing_error,
    ollama_json_schema,
)


class OllamaSchemaTests(TestCase):
    def test_project_requests_force_full_gpu_and_permanent_residency(self) -> None:
        payload = {"options": {"num_ctx": 16_384}, "keep_alive": "20m"}

        result = OllamaAnalyzer._force_gpu_residency(payload)

        self.assertEqual(result["options"]["num_gpu"], -1)
        self.assertEqual(result["options"]["num_ctx"], 16_384)
        self.assertEqual(result["keep_alive"], -1)

    def test_schema_keeps_structure_and_removes_unsupported_constraints(self) -> None:
        schema = ollama_json_schema(AnalysisResult)

        def assert_supported(value: object, parent_key: str | None = None) -> None:
            if isinstance(value, dict):
                if parent_key not in {"$defs", "properties"}:
                    self.assertTrue(OLLAMA_UNSUPPORTED_SCHEMA_KEYS.isdisjoint(value))
                for key, item in value.items():
                    assert_supported(item, key)
            elif isinstance(value, list):
                for item in value:
                    assert_supported(item, parent_key)

        self.assertEqual(schema["type"], "object")
        self.assertIn("tasks", schema["properties"])
        self.assertIn("meeting_result", schema["properties"])
        self.assertIn("title", schema["$defs"]["ExtractedTask"]["properties"])
        assert_supported(schema)

    def test_formalized_task_schema_is_supported_by_ollama(self) -> None:
        schema = ollama_json_schema(FormalizedTask)

        self.assertEqual(schema["type"], "object")
        self.assertIn("title", schema["properties"])
        self.assertIn("priority", schema["properties"])
        self.assertIn("due_at", schema["properties"])
        self.assertIn("due_expression", schema["properties"])
        self.assertIn("due_at", schema["required"])
        self.assertIn("due_at", ollama_json_schema(ResolvedDue)["required"])

    def test_grounded_chat_schema_is_supported_by_ollama(self) -> None:
        schema = ollama_json_schema(GroundedChatAnswer)

        self.assertIn("answer", schema["properties"])
        self.assertIn("used_reference_ids", schema["properties"])

    def test_semantic_analysis_schema_contains_search_dimensions(self) -> None:
        schema = ollama_json_schema(SemanticAnalysis)

        self.assertIn("categories", schema["properties"])
        self.assertIn("keywords", schema["properties"])
        self.assertIn("agreements", schema["properties"])
        self.assertIn("summary", schema["required"])
        self.assertIn("thread_summary", schema["required"])

    def test_archive_analysis_can_detect_participant_meeting_summary(self) -> None:
        schema = ollama_json_schema(ArchiveAnalysis)

        self.assertIn("meeting_result", schema["properties"])
        self.assertIn("detected", schema["$defs"]["MeetingResultSignal"]["properties"])
        self.assertIn("mailing", schema["properties"])

    def test_meeting_topic_match_schema_is_supported(self) -> None:
        schema = ollama_json_schema(MeetingTopicMatch)

        self.assertIn("matches", schema["properties"])
        self.assertIn("confidence", schema["properties"])


class MeetingTopicMatchTests(IsolatedAsyncioTestCase):
    async def test_topic_match_uses_semantic_summary_and_zero_temperature(self) -> None:
        analyzer = OllamaAnalyzer.__new__(OllamaAnalyzer)
        analyzer.config = type(
            "Config",
            (),
            {
                "llm": type(
                    "Llm",
                    (),
                    {"model": "synthetic", "context_length": 16_384},
                )()
            },
        )()
        request = httpx.Request("POST", "http://ollama/api/chat")
        analyzer._post_chat = AsyncMock(
            return_value=httpx.Response(
                200,
                request=request,
                json={
                    "message": {
                        "content": ('{"matches":true,"confidence":0.9,"evidence":"Один проект"}')
                    }
                },
            )
        )

        result = await analyzer.match_meeting_topic(
            "Проект Orion",
            "Обсуждение релиза",
            SemanticAnalysis(
                summary="Обсудили релиз Orion",
                thread_summary="Обсудили релиз Orion",
                keywords=["Orion", "релиз"],
            ),
        )

        self.assertTrue(result.matches)
        payload = analyzer._post_chat.await_args.args[0]
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertIn("Обсудили релиз Orion", payload["messages"][1]["content"])

    def test_mailing_batch_schema_contains_event_decisions(self) -> None:
        schema = ollama_json_schema(MailingBatchResult)

        self.assertIn("decisions", schema["properties"])
        self.assertIn("event_id", schema["$defs"]["MailingDecision"]["properties"])

    def test_focused_task_extraction_schema_contains_task_fields(self) -> None:
        schema = ollama_json_schema(TaskExtractionResult)

        self.assertIn("tasks", schema["properties"])
        self.assertIn("assignee", schema["$defs"]["ExtractedTask"]["properties"])

    def test_generated_semantic_lists_are_capped_instead_of_rejected(self) -> None:
        analysis = ArchiveAnalysis.model_validate(
            {
                "summary": "Итоги",
                "thread_summary": "Итоги встречи",
                "people": [f"Участник {index}" for index in range(43)],
            }
        )

        self.assertEqual(len(analysis.people), 30)

    def test_relevance_selection_schema_is_supported_by_ollama(self) -> None:
        schema = ollama_json_schema(RelevantReferenceSelection)

        self.assertIn("reference_ids", schema["properties"])

    def test_client_errors_are_not_retried_but_server_errors_are(self) -> None:
        request = httpx.Request("POST", "http://ollama/api/chat")
        client_error = httpx.HTTPStatusError(
            "bad request",
            request=request,
            response=httpx.Response(400, request=request),
        )
        server_error = httpx.HTTPStatusError(
            "server error",
            request=request,
            response=httpx.Response(500, request=request),
        )

        self.assertFalse(_retryable_ollama_error(client_error))
        self.assertTrue(_retryable_ollama_error(server_error))

    def test_invalid_structured_model_response_is_retried(self) -> None:
        with self.assertRaises(ValidationError) as raised:
            AnalysisResult.model_validate({"tasks": "not-a-list"})

        self.assertTrue(_retryable_ollama_error(raised.exception))
        self.assertTrue(is_ollama_processing_error(raised.exception))

    def test_all_ollama_http_failures_can_be_queued_for_later(self) -> None:
        request = httpx.Request("POST", "http://ollama/api/chat")
        unavailable = httpx.ConnectError("unavailable", request=request)
        configuration_error = httpx.HTTPStatusError(
            "model unavailable",
            request=request,
            response=httpx.Response(404, request=request),
        )

        self.assertTrue(is_ollama_processing_error(unavailable))
        self.assertTrue(is_ollama_processing_error(configuration_error))


class AssignmentPromptTests(IsolatedAsyncioTestCase):
    async def test_task_extraction_receives_identity_and_namesake_context(self):
        import json
        from datetime import UTC, datetime
        from unittest.mock import AsyncMock

        from improver.config import AppConfig, IdentityConfig
        from improver.models import CommunicationEvent
        from improver.services.assignment import assignment_signals

        config = AppConfig(
            identity=IdentityConfig(names=["Иван Петров"], addresses=["ivan@example.test"])
        )
        event = CommunicationEvent(
            body="Иван, подготовь договор",
            author="sender@example.test",
            participants=[
                {"name": "Иван Петров", "address": "ivan@example.test"},
                {"name": "Иван Сидоров", "address": "other@example.test"},
            ],
        )
        context = [
            {
                "occurred_at": "2026-09-14",
                "author": "ivan@example.test",
                "body": "Я подготовлю договор",
                "participants": event.participants,
            }
        ]
        analyzer = OllamaAnalyzer(config)
        analyzer._post_chat = AsyncMock(
            return_value=httpx.Response(
                200,
                request=httpx.Request("POST", "http://synthetic/api/chat"),
                json={"message": {"content": '{"tasks": []}'}},
            )
        )
        await analyzer.extract_tasks(
            event, [], context, assignment_signals(event, config.identity), datetime.now(UTC), "UTC"
        )
        payload = analyzer._post_chat.call_args.args[0]
        body = json.loads(payload["messages"][1]["content"])
        self.assertTrue(body["assignment_signals"]["name_ambiguous"])
        self.assertEqual(body["user_identity"]["addresses"], ["ivan@example.test"])
        self.assertEqual(body["previous_events_in_thread"], context)
        self.assertIn("assignment_evidence", payload["messages"][0]["content"])
        self.assertIn("Согласование X", payload["messages"][0]["content"])
        self.assertIn("уже завершено", payload["messages"][0]["content"])
        task_schema = payload["format"]["$defs"]["ExtractedTask"]["properties"]
        self.assertIn("assignee_address", task_schema)
        self.assertIn("assignment_evidence", task_schema)
