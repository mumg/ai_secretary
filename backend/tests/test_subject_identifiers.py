from unittest import TestCase

from improver.services.email_subjects import (
    comparison_tokens,
    email_thread_headers,
    subject_classification,
    subject_classification_for_llm,
    subject_hit_rate,
)
from improver.services.subject_identifiers import identifiers_conflict


def tokens(subject):
    return subject_classification(subject)["tokens"]


class SubjectIdentifierTests(TestCase):
    def test_formats_context_and_numeric_roles(self):
        cases = [
            ("INC000021715420 - Дополните сведения", "INC000021715420", "identifier", "primary"),
            ("Продукт BI_2638", "BI_2638", "identifier", "primary"),
            ("Капабилити CPB.039", "CPB.039", "identifier", "primary"),
            (
                "550e8400-e29b-41d4-a716-446655440000",
                "550e8400-e29b-41d4-a716-446655440000",
                "identifier",
                "primary",
            ),
            ("задача №00149053", "00149053", "identifier", "primary"),
            ("по договору 145700", "145700", "identifier", "primary"),
            ("договор №17/09/2026", "17/09/2026", "identifier", "primary"),
            ("срок договора 17/09/2026", "17/09/2026", "date", None),
            ("дополнение к задаче 143227", "143227", "identifier", "related"),
            ("ссылка на INC000021715420", "INC000021715420", "identifier", "related"),
            ("149053", "149053", "unknown", None),
            ("145700 Перенос файлов", "145700", "unknown", None),
            ("17.09.2026", "17.09.2026", "date", None),
            ("2026-09-17", "2026-09-17", "date", None),
            ("2026/09/17", "2026/09/17", "date", None),
            ("17/09/2026", "17/09/2026", "date", None),
            ("14:30", "14:30", "time", None),
            ("v0.1.7", "v0.1.7", "version", None),
            ("релиз 2.4", "2.4", "version", None),
            ("сентябрь 2026", "2026", "period", None),
            ("отчёт за 2026 г.", "2026", "period", None),
            ("3 вопроса", "3", "quantity", None),
            ("этап 2", "2", "quantity", None),
            ("SAP", "SAP", "word", None),
            ("B2B", "B2B", "unknown", None),
            ("SAP4HANA", "SAP4HANA", "unknown", None),
        ]
        for subject, token, kind, role in cases:
            with self.subTest(subject=subject):
                item = next(item for item in tokens(subject) if item["token"] == token)
                self.assertEqual(item["kind"], kind)
                self.assertEqual(item["role"], role)
                self.assertTrue(item["reason"])

    def test_leading_zeros_and_compound_identifiers_are_preserved(self):
        classified = tokens("Re: Заявка №000123 по BI_2638")
        self.assertEqual(
            next(t for t in classified if t["token"] == "000123")["normalized"], "000123"
        )
        self.assertEqual(
            next(t for t in classified if t["token"] == "BI_2638")["normalized"], "bi_2638"
        )

    def test_only_conflicting_main_identifiers_of_same_type_block(self):
        for first, second, conflict in [
            ("задача №123456", "задача №123457", True),
            ("INC000021715420", "inc000021715421", True),
            ("Продукт BI_2638", "Продукт BI_2639", True),
            ("договор 123456", "задача 123457", False),
            ("задача 123456 дополнение к 111111", "задача 123456 дополнение к 222222", False),
            ("задача 123456", "дополнение к задаче 123457", False),
            ("149053", "149054", False),
            ("версия 2.4", "версия 2.5", False),
            ("17.09.2026", "18.09.2026", False),
            ("этап 2", "этап 3", False),
        ]:
            with self.subTest(first=first, second=second):
                self.assertEqual(identifiers_conflict(tokens(first), tokens(second)), conflict)

    def test_numeric_details_do_not_destroy_topic_similarity(self):
        for first, second in [
            (
                "Согласование договора поставки оборудования 17.09.2026",
                "Согласование договора поставки оборудования 18.09.2026",
            ),
            ("План тестирования сервиса версия 0.1.7", "План тестирования сервиса версия 0.1.8"),
            (
                "задача 145700 перенос файлов дополнение к 143227",
                "задача 145700 перенос файлов дополнение к 143228",
            ),
        ]:
            with self.subTest(first=first):
                self.assertEqual(
                    subject_hit_rate(
                        comparison_tokens(tokens(first)), comparison_tokens(tokens(second))
                    ),
                    1,
                )

    def test_refresh_preserves_decision_and_native_calendar_link(self):
        previous = {"Original-Thread-Id": "exchange-id", "Subject-Thread-Match": {"version": 2}}
        updated = email_thread_headers("задача 123456", {"Importance": "high"}, previous)
        self.assertEqual(updated["Original-Thread-Id"], "exchange-id")
        self.assertEqual(updated["Subject-Thread-Match"], previous["Subject-Thread-Match"])
        self.assertEqual(updated["Importance"], "high")
        self.assertEqual(updated["Subject-Token-Classification"]["version"], 1)

    def test_model_receives_bounded_classification_without_repeating_words(self):
        result = subject_classification_for_llm(
            "задача №123456 " + " ".join(str(i) for i in range(100))
        )
        self.assertEqual(len(result["tokens"]), 24)
        self.assertEqual(result["tokens"][0]["kind"], "identifier")
        self.assertEqual(result["tokens"][0]["role"], "primary")
        self.assertTrue(all(t["kind"] != "word" for t in result["tokens"]))
