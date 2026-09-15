from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from improver.config import AppConfig, load_config
from improver.models import SystemSetting
from improver.services.notifications import NotificationService
from improver.services.settings import (
    load_runtime_config,
    runtime_payload,
    validate_runtime_payload,
)


class LocalWebConfigTests(TestCase):
    def test_local_launcher_sets_loopback_url(self):
        with patch.dict("os.environ", {"LOCAL_WEB_ONLY": "true"}):
            config = load_config()
        self.assertTrue(config.server.local_web_only)
        self.assertEqual(config.server.public_url, "http://127.0.0.1:8000")

    def test_default_still_uses_https(self):
        with patch.dict("os.environ", {"LOCAL_WEB_ONLY": "false"}):
            config = load_config()
        self.assertFalse(config.server.local_web_only)
        self.assertEqual(config.server.public_url, "https://localhost")

    def test_http_is_allowed_only_for_loopback(self):
        for url in ("http://127.0.0.1:8000", "http://localhost:8000"):
            self.assertEqual(AppConfig(server={"public_url": url}).server.public_url, url)
        for url in (
            "http://0.0.0.0:8000",
            "http://192.168.9.108:8000",
            "http://localhost.evil.test:8000",
            "http://user@localhost:8000",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                AppConfig(server={"public_url": url})

    def test_deployment_mode_is_not_a_writable_runtime_setting(self):
        config = AppConfig(server={"local_web_only": True, "public_url": "http://127.0.0.1:8000"})
        self.assertNotIn("local_web_only", runtime_payload(config)["server"])
        with patch("improver.services.settings.get_config", return_value=AppConfig()):
            saved = validate_runtime_payload({"server": {"local_web_only": True}})
        self.assertNotIn("local_web_only", saved["server"])


class LocalWebRuntimeTests(IsolatedAsyncioTestCase):
    async def test_saved_settings_cannot_change_launcher_mode(self):
        for enabled in (False, True):
            config = AppConfig(server={"local_web_only": enabled})
            session = Mock()
            session.get = AsyncMock(
                return_value=SystemSetting(
                    payload={
                        "server": {
                            "local_web_only": not enabled,
                            "public_url": "https://server.example.test",
                        }
                    }
                )
            )
            rows = Mock()
            rows.scalars.return_value = []
            session.execute = AsyncMock(return_value=rows)
            with patch("improver.services.settings.get_config", return_value=config):
                loaded = await load_runtime_config(session)
            self.assertEqual(loaded.server.local_web_only, enabled)
            self.assertEqual(
                loaded.server.public_url,
                "http://127.0.0.1:8000" if enabled else "https://server.example.test",
            )

    async def test_local_mode_never_initializes_or_sends_firebase(self):
        config = AppConfig(
            server={"local_web_only": True},
            notifications={
                "firebase_credentials": {"project_id": "previously-configured-project"},
            },
        )
        service = NotificationService(config)
        self.assertFalse(service._initialize())
        session = Mock()
        with patch.object(service, "_initialize", side_effect=AssertionError("FCM attempted")):
            self.assertEqual(await service.send(session, "NEW_TASK", "task-id"), 0)
        session.execute.assert_not_called()
