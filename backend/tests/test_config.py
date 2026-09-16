from unittest import TestCase
from unittest.mock import patch

from pydantic import ValidationError

from improver.config import AppConfig, load_config


class ConfigTests(TestCase):
    def test_context_can_be_configured_up_to_128k(self) -> None:
        config = AppConfig.model_validate({"llm": {"context_length": 131_072}})
        self.assertEqual(config.llm.context_length, 131_072)

    def test_context_above_128k_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AppConfig.model_validate({"llm": {"context_length": 131_073}})

    def test_legacy_config_path_is_not_required(self) -> None:
        with patch.dict("os.environ", {"CONFIG_PATH": "/missing/config.yaml"}):
            config = load_config()

        self.assertEqual(config.calendar.daily_plan_time, "08:00")

    def test_native_windows_endpoints_use_environment(self) -> None:
        with patch.dict("os.environ", {
            "LOCAL_WEB_ONLY": "true", "PUBLIC_URL": "http://127.0.0.1:18000",
            "DOCUMENT_PARSER_URL": "http://127.0.0.1:18080", "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        }, clear=True):
            config = load_config()
        self.assertTrue(config.server.local_web_only)
        self.assertEqual(config.server.public_url, "http://127.0.0.1:18000")
        self.assertEqual(config.document_parser.base_url, "http://127.0.0.1:18080")
        self.assertEqual(config.llm.base_url, "http://127.0.0.1:11434")

    def test_local_mode_rejects_public_host(self) -> None:
        with patch.dict("os.environ", {"LOCAL_WEB_ONLY": "true", "PUBLIC_URL": "https://public.example.org"}, clear=True):
            with self.assertRaises(ValueError):
                load_config()
