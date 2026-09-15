"""Offline tests: python3 -m unittest discover -s extensions/tasks_tabs_new_services/tests -p test_tabs_tasks_script.py."""

import copy
import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "sync_tabs_tasks.py"
SPEC = importlib.util.spec_from_file_location("sync_tabs_tasks", SCRIPT)
tabs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tabs)
BOOTSTRAP_SPEC = importlib.util.spec_from_file_location("tabs_run", SCRIPT.with_name("run.py"))
bootstrap = importlib.util.module_from_spec(BOOTSTRAP_SPEC)
with patch.dict("sys.modules", {"sync_tabs_tasks": tabs}):
    BOOTSTRAP_SPEC.loader.exec_module(bootstrap)


def fixture():
    return {
        "success": True, "code": 200, "data": {
            "snapshot": {
                "datasheetId": tabs.DATASHEET_ID,
                "meta": {"fieldMap": {
                    "status": {"name": "Статус согласования", "type": 3, "property": {
                        "options": [{"id": "new", "name": "Новый запрос"},
                                    {"id": "approved", "name": "Согласовано гл. архитектором"}],
                    }},
                    "cluster": {"name": "Кластер продукта", "type": 14, "property": {
                        "relatedLinkFieldId": "product", "lookUpTargetFieldId": "cluster_text",
                    }},
                    "product": {"name": "Реализующий продукт", "type": 7, "property": {
                        "foreignDatasheetId": "products",
                    }},
                    "name": {"name": "Название сервиса", "type": 1},
                    "desc": {"name": "Описание сервиса", "type": 1},
                    "number": {"name": "Номер согласования", "type": 20},
                }},
                "recordMap": {"rec1": {
                    "data": {"status": "new", "product": ["product1"],
                             "name": [{"text": "Перенос ", "type": 1},
                                      {"text": "номера", "type": 2}],
                             "desc": [{"text": "Описание\n", "type": 1}]},
                    "recordMeta": {"fieldUpdatedMap": {"number": {"autoNumber": 1945}}},
                    "createdAt": 1788355236000, "updatedAt": 1788355772971,
                }},
            },
            "foreignDatasheetMap": {"products": {"snapshot": {
                "meta": {"fieldMap": {"cluster_text": {"name": "Кластер", "type": 19}}},
                "recordMap": {"product1": {"data": {"cluster_text": [
                    {"text": tabs.CLUSTER, "type": 1},
                ]}}},
            }}},
        },
    }


class ParsingTests(unittest.TestCase):
    def test_resolve_cluster_select_text_and_autonumber(self):
        record, = tabs.DataPack(fixture()).candidates()
        self.assertEqual(record["name"], "Перенос номера")
        self.assertEqual(record["number"], "1945")
        self.assertEqual(record["description"], "Описание")

    def test_approved_and_other_cluster_are_excluded(self):
        data = fixture()
        data["data"]["snapshot"]["recordMap"]["rec1"]["data"]["status"] = "approved"
        self.assertEqual(tabs.DataPack(data).candidates(), [])
        data = fixture()
        product = data["data"]["foreignDatasheetMap"]["products"]["snapshot"]
        product["recordMap"]["product1"]["data"]["cluster_text"][0]["text"] = "Другой кластер"
        self.assertEqual(tabs.DataPack(data).candidates(), [])

    def test_missing_reference_and_unknown_option_fail_closed(self):
        data = fixture()
        data["data"]["foreignDatasheetMap"] = {}
        with self.assertRaises(tabs.DataError):
            tabs.DataPack(data).candidates()
        data = fixture()
        data["data"]["snapshot"]["recordMap"]["rec1"]["data"]["status"] = "unknown"
        with self.assertRaises(tabs.DataError):
            tabs.DataPack(data).candidates()

    def test_wrong_snapshot_rejected(self):
        data = fixture()
        data["data"]["snapshot"]["datasheetId"] = "other"
        with self.assertRaises(tabs.DataError):
            tabs.DataPack(data)

    def test_deadline_priority_and_timezone(self):
        now = datetime(2026, 9, 18, 17, 45, tzinfo=tabs.MOSCOW)
        task = tabs.make_task(tabs.DataPack(fixture()).candidates()[0], now)
        self.assertEqual(task["due_at"], "2026-09-21T17:45:00+03:00")
        self.assertEqual(task["priority"], "HIGH")
        self.assertEqual(task["status"], "NEW")
        self.assertEqual(task["occurred_at"], "2026-09-02T16:20:36+03:00")


class ScheduleTests(unittest.TestCase):
    def test_boundaries_and_weekend(self):
        for source, target, active in [
            ("2026-09-14T08:59:00", "2026-09-14T09:00:00", False),
            ("2026-09-14T09:00:00", "2026-09-14T09:15:00", True),
            ("2026-09-14T17:59:00", "2026-09-15T09:00:00", True),
            ("2026-09-14T18:00:00", "2026-09-15T09:00:00", False),
            ("2026-09-18T17:45:00", "2026-09-21T09:00:00", True),
            ("2026-09-19T12:00:00", "2026-09-21T09:00:00", False),
        ]:
            with self.subTest(source=source):
                now = datetime.fromisoformat(source).replace(tzinfo=tabs.MOSCOW)
                self.assertEqual(tabs.is_work_time(now), active)
                self.assertEqual(tabs.next_run(now).isoformat(), target + "+03:00")
        utc = datetime.fromisoformat("2026-09-14T06:00:00+00:00")
        self.assertTrue(tabs.is_work_time(utc))


class StatusTests(unittest.TestCase):
    def test_status_report_uses_component_api_and_numeric_metrics(self):
        with patch.object(tabs, "request_json") as request:
            tabs.report_status(
                "https://example.test/api/v1/system/components/tabs-loader",
                "tls-context",
                "ERROR",
                "Ошибка авторизации",
                {"attempts": 2},
            )

        method, url = request.call_args.args
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(method, "PUT")
        self.assertEqual(url.rsplit("/", 1)[-1], "tabs-loader")
        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(payload["metrics"], {"attempts": 2})
        self.assertEqual(payload["ttl_seconds"], 120)

    def test_auth_error_is_safe_and_actionable(self):
        error = tabs.HTTPError("https://tabs.example/secret", 401, "", {}, None)
        self.addCleanup(error.close)
        message = tabs.status_error_message(error)

        self.assertIn("авторизации", message)
        self.assertNotIn("secret", message)

    def test_last_poll_health_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.sqlite3"
            self.assertEqual(tabs.load_component_health(state)[0], "OK")

            tabs.save_component_health(state, "ERROR", "Ошибка авторизации")

            self.assertEqual(
                tabs.load_component_health(state),
                ("ERROR", "Ошибка авторизации"),
            )


class BootstrapTests(unittest.TestCase):
    def test_environment_bearer_auth_and_missing_token(self):
        with tempfile.TemporaryDirectory() as folder:
            cert = Path(folder) / "client.pem"
            key = Path(folder) / "client.key"
            cert.touch()
            key.touch()
            env = {
                "SECRETARY_API_URL": "https://example.test/api/v1",
                "SECRETARY_CLIENT_CERT": str(cert), "SECRETARY_CLIENT_KEY": str(key),
                "TABS_AUTHORIZATION": "Bearer test-token",
            }
            with patch.dict("os.environ", env, clear=True):
                config = bootstrap.load_config(Path(folder) / "missing.json")
                self.assertEqual(config["TABS_AUTHORIZATION"], "Bearer test-token")
                self.assertEqual(tabs.tabs_headers()["Authorization"], "Bearer test-token")
            del env["TABS_AUTHORIZATION"]
            with patch.dict("os.environ", env, clear=True), self.assertRaises(ValueError):
                bootstrap.load_config(Path(folder) / "missing.json")

    def test_existing_source_is_not_overwritten(self):
        config = {"SECRETARY_API_URL": "https://example.test/api/v1",
                  "SECRETARY_CLIENT_CERT": "cert", "SECRETARY_CLIENT_KEY": "key"}
        with patch.object(tabs, "tls_context"), patch.object(tabs, "request_json") as request:
            request.return_value = [{"id": bootstrap.SOURCE_ID, "enabled": False}]
            bootstrap.register_if_missing(config)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], "GET")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "state.sqlite3"
        self.records = tabs.DataPack(fixture()).candidates()
        self.now = datetime(2026, 9, 14, 10, tzinfo=tabs.MOSCOW)
        self.calls = []

    def acknowledge(self, payload):
        self.calls.append(copy.deepcopy(payload))
        self.assertFalse(payload["close_missing"])
        self.assertLessEqual(len(payload["tasks"]), 500)
        return {"items": [{"external_id": task["external_id"], "action": "created"}
                          for task in payload["tasks"]]}

    def test_restart_skips_sent_even_if_source_changes(self):
        with tabs.state_database(self.path) as db:
            tabs.deliver(db, "source", self.records, self.now, self.acknowledge)
        self.records[0]["updated_at"] += 1000
        with tabs.state_database(self.path) as db:
            tabs.deliver(db, "source", self.records, self.now + timedelta(days=1), self.acknowledge)
        self.assertEqual(len(self.calls), 1)

    def test_lost_ack_retries_identical_payload_after_restart(self):
        def timeout(payload):
            self.calls.append(copy.deepcopy(payload))
            raise TimeoutError

        with tabs.state_database(self.path) as db, self.assertRaises(TimeoutError):
            tabs.deliver(db, "source", self.records, self.now, timeout)
        with tabs.state_database(self.path) as db:
            tabs.deliver(db, "source", self.records, self.now + timedelta(days=1), self.acknowledge)
        self.assertEqual(self.calls[0], self.calls[1])

    def test_pending_no_longer_matching_is_not_sent(self):
        with tabs.state_database(self.path) as db:
            with self.assertRaises(tabs.DataError):
                tabs.deliver(db, "source", self.records, self.now, lambda _: {"items": []})
            tabs.deliver(db, "source", [], self.now, self.acknowledge)
        self.assertEqual(self.calls, [])

    def test_api_batch_limit_and_namespace(self):
        records = [{**self.records[0], "id": f"rec{i}"} for i in range(501)]
        with tabs.state_database(self.path) as db:
            tabs.deliver(db, "source", records, self.now, self.acknowledge)
            tabs.deliver(db, "another-source", self.records, self.now, self.acknowledge)
        self.assertEqual([len(p["tasks"]) for p in self.calls], [500, 1, 1])

    def test_exclusive_state_lock(self):
        with tabs.state_database(self.path):
            with self.assertRaises(BlockingIOError), tabs.state_database(self.path):
                self.fail("Second importer should not acquire the same state")


if __name__ == "__main__":
    unittest.main()
