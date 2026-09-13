from unittest import TestCase

from sqlalchemy.dialects import postgresql

from improver.services.full_text_search import (
    full_text_match,
    normalize_search_query,
)


class FullTextSearchTests(TestCase):
    def test_search_query_whitespace_is_normalized(self) -> None:
        self.assertEqual(
            normalize_search_query("  статус   проекта\nсегодня "),
            "статус проекта сегодня",
        )

    def test_condition_combines_russian_stemming_and_exact_tokens(self) -> None:
        condition = full_text_match("conversation_threads", "встреча example.test")
        sql = str(condition.compile(dialect=postgresql.dialect()))

        self.assertIn("conversation_threads.search_vector", sql)
        self.assertEqual(sql.count("websearch_to_tsquery("), 2)
        self.assertIn("@@", sql)

    def test_unknown_table_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            full_text_match("untrusted", "query")

    def test_meeting_results_are_searchable(self) -> None:
        condition = full_text_match("meeting_results", "решение встречи")
        sql = str(condition.compile(dialect=postgresql.dialect()))

        self.assertIn("meeting_results.search_vector", sql)

        child_condition = full_text_match("meeting_result_children", "письмо участника")
        child_sql = str(child_condition.compile(dialect=postgresql.dialect()))
        self.assertIn("meeting_result_children.search_vector", child_sql)
