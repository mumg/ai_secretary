from __future__ import annotations

from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.sql.elements import ColumnElement


def normalize_search_query(value: str | None) -> str:
    return " ".join((value or "").split())


def full_text_match(table_name: str, query: str) -> ColumnElement[bool]:
    """Match a generated search vector using Russian stemming and exact tokens."""
    if table_name not in {
        "tasks",
        "meetings",
        "meeting_results",
        "meeting_result_children",
        "conversation_threads",
    }:
        raise ValueError("Unsupported full-text search table")
    normalized = normalize_search_query(query)
    vector = literal_column(f"{table_name}.search_vector", type_=TSVECTOR())
    russian_query = func.websearch_to_tsquery("russian", normalized)
    exact_query = func.websearch_to_tsquery("simple", normalized)
    return vector.bool_op("@@")(russian_query.op("||")(exact_query))
