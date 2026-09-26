import json
import hashlib
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import engine
import comparison
import run

EXAMPLES = Path(__file__).resolve().parent / "examples"


class RegressionTests(unittest.TestCase):
    def test_reviewed_result_marks_changed_decisions_and_filter_counts(self):
        def saved(run_id, tasks, delegations, review=None):
            result = {"run_id": run_id, "input_sha256": "same-input", "review": review,
                      "suggestions": {"tasks": tasks, "delegations": delegations}}
            return result, {"case": "mail", "run_id": run_id}

        task = {"title": "Подготовить отчёт", "assignee": "user"}
        baseline = saved("20260925T100000Z-a", [task], [], {"verdict": "agree", "scope": "suggestions_v1"})
        same = saved("20260925T110000Z-b", [dict(task)], [])
        changed = saved("20260925T120000Z-c", [], [{"title": "Подготовить отчёт",
                                                      "assignee_name": "Иван"}])
        summaries = {item["run_id"]: item for item in comparison.annotate([baseline, same, changed])}
        self.assertEqual(summaries[same[1]["run_id"]]["comparison"], "matched")
        self.assertEqual(summaries[changed[1]["run_id"]]["comparison"], "diverged")
        self.assertEqual(summaries[changed[1]["run_id"]]["delegation_count"], 1)

    def test_disagreement_uses_expected_titles(self):
        baseline = ({"run_id": "20260925T100000Z-a", "input_sha256": "same-input",
                     "review": {"verdict": "disagree", "scope": "suggestions_v1",
                                "expected_tasks": "- Подготовить отчёт",
                                "expected_delegations": ""},
                     "suggestions": {"tasks": [], "delegations": []}},
                    {"case": "mail", "run_id": "20260925T100000Z-a"})
        corrected = ({"run_id": "20260925T110000Z-b", "input_sha256": "same-input",
                      "review": None, "suggestions": {"tasks": [{"title": "Подготовить отчёт",
                                                                   "assignee": "user"}],
                                                       "delegations": []}},
                     {"case": "mail", "run_id": "20260925T110000Z-b"})
        summaries = comparison.annotate([baseline, corrected])
        self.assertEqual(summaries[0]["comparison"], "matched")

    def test_standalone_config_supports_private_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"llm": {"provider": "ollama",
                                                "base_url": "http://192.168.9.108:11434",
                                                "model": "test"}}), encoding="utf-8")
            config = engine.llm_config(path)
        self.assertEqual(config["model"], "test")
        self.assertEqual(config["base_url"], "http://192.168.9.108:11434")

    def test_status_report_exposes_false_delegation_for_review(self):
        case = engine.load_case(EXAMPLES / "status-report.example.json")
        answer = {"items": [{"title": "Собрать перечень", "assignee_name": "Артем",
                             "assignee_email": "artem@example.test", "is_new": True,
                             "evidence": "Собрать перечень методов, используемых в ЕО", "confidence": .99}]}
        result = engine.legacy_suggestions({"analysis": {"tasks": []}, "delegations": answer})
        self.assertEqual(len(result["delegations"]), 1)
        self.assertEqual(result["delegations"][0]["title"], "Собрать перечень")

    def test_direct_request_is_shown_as_model_suggestion(self):
        case = engine.load_case(EXAMPLES / "direct-assignment.example.json")
        result = engine.legacy_suggestions({"analysis": {"tasks": []},
                                            "delegations": {"items": [{"title": "Отчёт", "assignee_name": "Иван Петров",
                                                                         "assignee_email": "ivan@example.test", "is_new": True,
                                                                         "evidence": "Иван, подготовь, пожалуйста, отчёт о поставках к пятнице.",
                                                                         "confidence": .98}]}})
        self.assertEqual(result["delegations"][0]["assignee_name"], "Иван Петров")

    def test_status_email_exposes_all_model_suggestions(self):
        case = engine.load_case(EXAMPLES / "status-report.example.json")
        tasks = {"items": []}
        delegation = {"items": [{"title": "Собрать перечень", "assignee_name": "Артем",
                                 "assignee_email": "artem@example.test", "is_new": True,
                                 "evidence": "Собрать перечень методов, используемых в ЕО", "confidence": .99}]}
        config = {"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "test",
                  "temperature": .1, "context_length": 16384, "auto_create_confidence": .85}
        with patch.object(engine, "ask", side_effect=[tasks, delegation]) as llm:
            result = engine.analyze(case, config, "")
        self.assertEqual([call.args[2] for call in llm.call_args_list],
                         ["TaskExtractionResult", "DelegationExtractionResult"])
        self.assertEqual(result["suggestions"]["tasks"], [])
        self.assertEqual(len(result["suggestions"]["delegations"]), 1)

    def test_run_and_review_save_one_result_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input").mkdir()
            sample = engine.load_case(EXAMPLES / "status-report.example.json")
            (root / "input" / "status.json").write_text(json.dumps(sample), encoding="utf-8")
            fake = {"input": sample, "input_sha256": engine.digest(sample), "ran_at": "2026-09-25T10:00:00Z",
                    "model": {"provider": "ollama", "model": "test"}, "raw": {},
                    "contracts_sha256": hashlib.sha256(engine.CONTRACTS.read_bytes()).hexdigest(),
                    "engine_sha256": hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest(),
                    "suggestions": {"tasks": [], "delegations": []}, "review": None}
            with patch.object(run, "INPUT", root / "input"), patch.object(run, "RESULTS", root / "results"), \
                 patch.object(run, "analyze", return_value=fake):
                result = run.run_case("status", {"provider": "ollama"}, "")
                run.Handler.config, run.Handler.api_key = {"provider": "ollama"}, ""
                server = ThreadingHTTPServer(("127.0.0.1", 0), run.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{server.server_port}/api/review"
                    body = {"case": "status", "run_id": result["run_id"], "verdict": "disagree",
                            "reason": "Это статус руководителю", "expected_tasks": "нет", "expected_delegations": "нет"}
                    req = Request(url, data=json.dumps(body).encode(), method="POST",
                                  headers={"Origin": f"http://127.0.0.1:{server.server_port}",
                                           "Content-Type": "application/json"})
                    with urlopen(req) as response:
                        self.assertEqual(response.status, 200)
                    saved = json.loads((root / "results" / "status" / (result["run_id"] + ".json")).read_text())
                    self.assertEqual(saved["review"]["reason"], "Это статус руководителю")
                    self.assertEqual(saved["review"]["scope"], "suggestions_v1")
                    self.assertEqual(saved["input_sha256"], engine.digest(sample))
                    self.assertIsNotNone(run.cases("test")[0]["current_result"])
                    self.assertIsNone(run.cases("other-model")[0]["current_result"])
                    with urlopen(url.replace("/api/review", f"/api/results/status/{result['run_id']}")) as response:
                        detail = json.load(response)
                    self.assertEqual(detail["comparison"]["status"], "diverged")
                finally:
                    server.shutdown()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
