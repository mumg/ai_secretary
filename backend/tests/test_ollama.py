from unittest import TestCase

import httpx
from pydantic import ValidationError

from improver.services.ollama import (
    OLLAMA_UNSUPPORTED_SCHEMA_KEYS,
    AnalysisResult,
    ArchiveAnalysis,
    FormalizedTask,
    GroundedChatAnswer,
    MailingBatchResult,
    RelevantReferenceSelection,
    ResolvedDue,
    SemanticAnalysis,
    TaskExtractionResult,
    _retryable_ollama_error,
    is_ollama_processing_error,
    ollama_json_schema,
)


class OllamaSchemaTests(TestCase):
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
