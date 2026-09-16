import json
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from improver.config import AppConfig
from improver.db import get_session
from improver.main import app
from improver.models import SystemSetting
from improver.services.ollama import OllamaAnalyzer, OllamaResponseError
from improver.services.settings import load_runtime_config
from improver.services.system_status import _ollama_component


@asynccontextmanager
async def no_slot(**kwargs):
    yield


class LlmTransportTests(IsolatedAsyncioTestCase):
    def config(self, provider="openai", url="https://model.example.test/v1"):
        return AppConfig.model_validate(
            {
                "llm": {
                    "provider": provider,
                    "base_url": url,
                    "model": "qwen3-235b-a22b-instruct-2507",
                    "api_key": "test-key-only",
                }
            }
        )

    def client_patch(self, handler):
        client = httpx.AsyncClient
        return patch(
            "httpx.AsyncClient",
            side_effect=lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler)),
        )

    async def test_authenticated_structured_request_and_response(self):
        for url in ("https://model.example.test/v1/", "https://model.example.test"):
            config = self.config(url=url)

            def handler(request, config=config):
                self.assertEqual(str(request.url), "https://model.example.test/v1/chat/completions")
                self.assertEqual(request.headers["Authorization"], "Bearer test-key-only")
                body = json.loads(request.content)
                self.assertEqual(body["model"], config.llm.model)
                self.assertEqual(body["response_format"]["json_schema"]["schema"]["type"], "object")
                self.assertEqual(body["max_tokens"], 512)
                for field in ("options", "think", "format", "keep_alive"):
                    self.assertNotIn(field, body)
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"role": "assistant", "content": '{"tasks": []}'}}]
                    },
                )

            with (
                self.client_patch(handler),
                patch("improver.services.ollama.ollama_request_slot", no_slot),
            ):
                result = await OllamaAnalyzer(config)._post_chat(
                    {
                        "model": config.llm.model,
                        "messages": [{"role": "user", "content": "Test"}],
                        "format": {"type": "object"},
                        "think": False,
                        "options": {"num_predict": 512, "num_ctx": 16384, "temperature": 0},
                    }
                )
                self.assertEqual(result.json()["message"]["content"], '{"tasks": []}')

    async def test_ollama_protocol_and_optional_auth_are_preserved(self):
        for key in (None, "test-key-only"):
            config = self.config("ollama", "http://ollama:11434")
            if key is None:
                config.llm.api_key = None

            def handler(request, key=key):
                self.assertEqual(str(request.url), "http://ollama:11434/api/chat")
                self.assertEqual(
                    request.headers.get("Authorization"), f"Bearer {key}" if key else None
                )
                body = json.loads(request.content)
                self.assertEqual(body["options"]["num_gpu"], -1)
                self.assertEqual(body["keep_alive"], -1)
                return httpx.Response(200, json={"message": {"content": "OK"}})

            with (
                self.client_patch(handler),
                patch("improver.services.ollama.ollama_request_slot", no_slot),
            ):
                result = await OllamaAnalyzer(config)._post_chat({"model": "qwen", "messages": []})
                self.assertEqual(result.json()["message"]["content"], "OK")

    async def test_openai_sse_stream_with_comments_usage_and_done(self):
        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-key-only")
            self.assertTrue(json.loads(request.content)["stream"])
            return httpx.Response(
                200,
                text=': heartbeat\n\ndata: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"При"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"вет"}}]}\n\n'
                'data: {"choices":[],"usage":{"total_tokens":5}}\n\ndata: [DONE]\n\n'
                'data: {"choices":[{"delta":{"content":"AFTER DONE"}}]}\n\n',
            )

        with (
            self.client_patch(handler),
            patch("improver.services.ollama.ollama_request_slot", no_slot),
        ):
            chunks = [
                chunk
                async for chunk in OllamaAnalyzer(self.config()).stream_answer_from_archive(
                    "Привет", [], [], datetime.now(UTC), "UTC"
                )
            ]
        self.assertEqual("".join(chunks), "Привет")

    async def test_http_error_body_cannot_echo_key_into_processing_errors(self):
        def handler(request):
            return httpx.Response(401, text="Invalid key: test-key-only")

        with (
            self.client_patch(handler),
            patch("improver.services.ollama.ollama_request_slot", no_slot),
        ):
            result = await OllamaAnalyzer(self.config())._post_chat(
                {"model": "qwen", "messages": []}
            )
        self.assertEqual(result.status_code, 401)
        self.assertNotIn("test-key-only", result.text)

    async def test_invalid_success_response_is_not_an_empty_answer(self):
        with (
            self.client_patch(lambda request: httpx.Response(200, json={"choices": []})),
            patch("improver.services.ollama.ollama_request_slot", no_slot),
        ):
            with self.assertRaises(OllamaResponseError):
                await OllamaAnalyzer(self.config())._post_chat({"model": "qwen", "messages": []})

    async def test_status_uses_authenticated_models_endpoint(self):
        config = self.config()

        def handler(request):
            self.assertEqual(str(request.url), "https://model.example.test/v1/models")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key-only")
            return httpx.Response(200, json={"data": [{"id": config.llm.model}]})

        with self.client_patch(handler):
            result = await _ollama_component(config, datetime.now(UTC))
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.label, "LLM")


@skipUnless(os.getenv("WEB_TEST_DATABASE_URL"), "isolated PostgreSQL URL not set")
class LlmSettingsTests(IsolatedAsyncioTestCase):
    async def test_key_save_preserve_rotate_clear_and_redaction(self):
        engine = create_async_engine(os.environ["WEB_TEST_DATABASE_URL"])
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "key"
            key_file.write_text("test-master-key-longer-than-32-characters")
            with patch.dict(os.environ, {"APP_MASTER_KEY_FILE": str(key_file)}):
                async with engine.connect() as connection:
                    transaction = await connection.begin()
                    factory = async_sessionmaker(
                        connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
                    )

                    async def sessions():
                        async with factory() as session:
                            yield session

                    app.dependency_overrides[get_session] = sessions
                    try:
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=app), base_url="http://test"
                        ) as client:

                            async def save(**fields):
                                response = await client.put(
                                    "/api/v1/admin/settings",
                                    json={
                                        "settings": {
                                            "llm": {
                                                "provider": "openai",
                                                "base_url": "https://model.example.test/v1",
                                            }
                                        },
                                        **fields,
                                    },
                                )
                                self.assertEqual(response.status_code, 200, response.text)
                                self.assertNotIn("secret-value", response.text)
                                self.assertNotIn("llm_api_key_encrypted", response.text)
                                return response.json()

                            result = await save(llm_api_key="secret-value-one")
                            self.assertTrue(result["llm_api_key_configured"])
                            async with factory() as session:
                                row = await session.get(SystemSetting, 1)
                                ciphertext = row.payload["llm_api_key_encrypted"]
                                self.assertNotIn("secret-value", json.dumps(row.payload))
                                config = await load_runtime_config(session)
                                self.assertEqual(
                                    config.llm.api_key.get_secret_value(), "secret-value-one"
                                )
                                self.assertNotIn("secret-value", repr(config))
                            await save()
                            await save(llm_api_key="")
                            async with factory() as session:
                                row = await session.get(SystemSetting, 1)
                                self.assertEqual(row.payload["llm_api_key_encrypted"], ciphertext)
                            await save(llm_api_key="secret-value-two")
                            async with factory() as session:
                                config = await load_runtime_config(session)
                                self.assertEqual(
                                    config.llm.api_key.get_secret_value(), "secret-value-two"
                                )
                            for key, clear in (
                                ("secret-value-three", True),
                                ("secret-value\ninvalid", False),
                                ("secret-value\x00invalid", False),
                            ):
                                response = await client.put(
                                    "/api/v1/admin/settings",
                                    json={
                                        "settings": {},
                                        "llm_api_key": key,
                                        "clear_llm_api_key": clear,
                                    },
                                )
                                self.assertEqual(response.status_code, 422)
                                self.assertNotIn("secret-value", response.text)
                            result = await save(clear_llm_api_key=True)
                            self.assertFalse(result["llm_api_key_configured"])
                            async with factory() as session:
                                config = await load_runtime_config(session)
                                self.assertEqual(config.llm.request_headers(), {})
                            result = (await client.get("/api/v1/admin/settings")).json()
                            self.assertFalse(result["llm_api_key_configured"])
                    finally:
                        app.dependency_overrides.clear()
                        await transaction.rollback()
        await engine.dispose()
