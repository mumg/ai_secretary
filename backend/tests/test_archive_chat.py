from unittest import TestCase

from improver.services.archive_chat import (
    expand_search_terms,
    query_scope,
    required_entity_terms,
    search_tokens,
)


class ArchiveChatSearchTests(TestCase):
    def test_search_tokens_remove_question_words_and_duplicates(self) -> None:
        self.assertEqual(
            search_tokens("Что писал Иван про Иван и договор Ozon?"),
            ["писал", "иван", "договор", "ozon"],
        )

    def test_search_tokens_keep_email_address(self) -> None:
        self.assertEqual(
            search_tokens("Письма от user@example.com"), ["user@example.com"]
        )

    def test_organization_query_ignores_generic_words_and_adds_transliteration(self) -> None:
        query = "Какие письма я получал от Озон?"

        self.assertEqual(search_tokens(query), ["озон"])
        self.assertEqual(expand_search_terms(search_tokens(query)), ["озон", "ozon"])
        self.assertEqual(required_entity_terms(query), ["озон", "ozon"])
        self.assertEqual(query_scope(query), "events")

    def test_query_scope_distinguishes_tasks_and_mixed_queries(self) -> None:
        self.assertEqual(query_scope("Покажи задачи по договору"), "tasks")
        self.assertEqual(query_scope("Какие задачи появились из писем?"), "all")

    def test_person_after_with_is_normalized_for_semantic_index(self) -> None:
        terms = required_entity_terms(
            "О чем мы договорились на встрече с Ивановым Иваном в пятницу?"
        )

        self.assertIn("иванов", terms)
        self.assertIn("ivanov", terms)
