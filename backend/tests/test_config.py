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
