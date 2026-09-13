import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from improver.config import AppConfig
from improver.services.settings import (
    SecretCipher,
    identity_addresses_from_sources,
    runtime_payload,
    validate_runtime_payload,
)


class SettingsTests(TestCase):
    def test_secret_cipher_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "master-key"
            key_file.write_text("a-secure-test-key-with-more-than-32-characters", encoding="utf-8")
            with patch.dict(os.environ, {"APP_MASTER_KEY_FILE": str(key_file)}):
                cipher = SecretCipher()
                encrypted = cipher.encrypt("mail password")

                self.assertNotIn("mail password", encrypted)
                self.assertEqual(cipher.decrypt(encrypted), "mail password")

    def test_runtime_payload_does_not_expose_secrets_or_bootstrap_settings(self) -> None:
        config = AppConfig.model_validate(
            {
                "notifications": {"firebase_credentials": {"project_id": "private"}},
                "communication_sources": {
                    "items": [
                        {
                            "id": "mail",
                            "type": "imap",
                            "enabled": False,
                            "credential": "private",
                        }
                    ]
                },
                "identity": {"addresses": ["user@example.test"], "names": ["User"]},
            }
        )

        payload = runtime_payload(config)

        self.assertNotIn("database", payload)
        self.assertNotIn("data_dir", payload["server"])
        self.assertNotIn("firebase_credentials", payload["notifications"])
        self.assertNotIn("items", payload["communication_sources"])
        self.assertNotIn("addresses", payload["identity"])
        self.assertEqual(payload["identity"]["names"], ["User"])

    def test_identity_addresses_are_derived_from_source_settings(self) -> None:
        addresses = identity_addresses_from_sources(
            [
                {"type": "imap", "username": "User@example.test"},
                {
                    "type": "exchange",
                    "username": "DOMAIN\\user",
                    "primary_smtp_address": "user@example.test",
                },
                {"type": "imap", "username": "second@example.test"},
            ]
        )

        self.assertEqual(addresses, ["User@example.test", "second@example.test"])

    def test_runtime_context_accepts_128k(self) -> None:
        payload = validate_runtime_payload({"llm": {"context_length": 131_072}})

        self.assertEqual(payload["llm"]["context_length"], 131_072)

    def test_analysis_filters_are_normalized_and_persisted(self) -> None:
        payload = validate_runtime_payload(
            {
                "analysis_filters": {
                    "stop_words": ["  Автоматическое   уведомление ", "автоматическое уведомление"],
                    "excluded_addresses": [" NoReply@Example.test ", "noreply@example.test"],
                }
            }
        )

        self.assertEqual(payload["analysis_filters"]["stop_words"], ["Автоматическое уведомление"])
        self.assertEqual(
            payload["analysis_filters"]["excluded_addresses"],
            ["NoReply@Example.test"],
        )
