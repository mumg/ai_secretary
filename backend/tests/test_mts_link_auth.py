import importlib.util
import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from improver.config import AppConfig, SourceConfig
from improver.connectors.mts_link import MtsLinkConnector
from improver.models import Base, CommunicationSource
from improver.services import mts_link_auth as auth
from improver.services.settings import SecretCipher, source_config_from_record
from improver.services.source_credentials import pack_mts_tokens, unpack_credential


class CredentialTests(TestCase):
    def test_legacy_and_bundle_credentials_are_not_exposed_by_config(self):
        self.assertEqual(unpack_credential("mts_link", "legacy"), {"credential": "legacy"})
        bundle = pack_mts_tokens("access-secret", "refresh-secret")
        config = SourceConfig(
            id="mts",
            type="mts_link",
            base_url="https://gw.mts-link.ru",
            **unpack_credential("mts_link", bundle),
        )
        self.assertNotIn("secret", repr(config))
        self.assertNotIn("secret", config.model_dump_json())
        self.assertEqual(unpack_credential("imap", bundle), {"credential": bundle})


class TokenHttpTests(IsolatedAsyncioTestCase):
    async def test_text_plain_json_tokens_and_refresh_body(self):
        def handle(request):
            self.assertEqual(request.url.path, "/accountUcaas/AccountUcaas.Refresh")
            self.assertEqual(json.loads(request.content), {"refreshToken": "old-refresh"})
            return httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                text=json.dumps(
                    {
                        "type": "Tokens",
                        "value": {"accessToken": "new-access", "refreshToken": "new-refresh"},
                    }
                ),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(auth.httpx, "AsyncClient", return_value=client):
            tokens = await auth.exchange_tokens(
                "https://gw.mts-link.ru", "Refresh", {"refreshToken": "old-refresh"}
            )
        self.assertEqual(tokens.access_token, "new-access")
        self.assertNotIn("new-access", repr(tokens))

    async def test_errors_never_include_upstream_secrets_and_redirects_are_not_followed(self):
        for response in [
            httpx.Response(400, text="private-auth-code"),
            httpx.Response(302, headers={"Location": "https://evil.test/private-code"}),
            httpx.Response(200, json={"type": "Error", "value": "private-auth-code"}),
            httpx.Response(200, json=[]),
            httpx.Response(
                200,
                json={
                    "type": "Tokens",
                    "value": {"accessToken": "bad\nheader", "refreshToken": "refresh"},
                },
            ),
        ]:
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _, response=response: response)
            )
            with patch.object(auth.httpx, "AsyncClient", return_value=client):
                with self.assertRaises(auth.MtsLinkAuthError) as error:
                    await auth.exchange_tokens(
                        "https://gw.mts-link.ru",
                        "LoginByAuthCode",
                        {"authCode": "private-auth-code"},
                    )
                self.assertNotIn("private", str(error.exception))

    async def test_connector_retries_with_rotated_bearer_and_cookie_once(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(401 if len(requests) == 1 else 200, json={"id": 123})

        source = SourceConfig(
            id="mts",
            type="mts_link",
            base_url="https://gw.mts-link.ru",
            credential="old-access",
            refresh_token="old-refresh",
        )
        connector = MtsLinkConnector(source, AppConfig())
        refresh = AsyncMock(return_value=auth.MtsTokens("new-access", "new-refresh"))
        async with httpx.AsyncClient(
            base_url=source.base_url, transport=httpx.MockTransport(handle)
        ) as client:
            with patch("improver.connectors.mts_link.refresh_source_tokens", refresh):
                result = await connector._json(client, "/api/login")
        self.assertEqual(result, {"id": 123})
        refresh.assert_awaited_once_with(source, "old-access")
        self.assertEqual(requests[1].headers["Authorization"], "Bearer new-access")
        self.assertEqual(requests[1].headers["Cookie"], "access=new-access")

    async def test_persistent_401_does_not_loop_and_legacy_token_does_not_refresh(self):
        for refresh_token in [None, "old-refresh"]:
            source = SourceConfig(
                id="mts",
                type="mts_link",
                base_url="https://gw.mts-link.ru",
                credential="old",
                refresh_token=refresh_token,
            )
            refresh = AsyncMock(return_value=auth.MtsTokens("new", "next"))
            async with httpx.AsyncClient(
                base_url=source.base_url,
                transport=httpx.MockTransport(lambda _: httpx.Response(401)),
            ) as client:
                with patch("improver.connectors.mts_link.refresh_source_tokens", refresh):
                    with self.assertRaises(auth.MtsLinkAuthError):
                        await MtsLinkConnector(source, AppConfig())._json(client, "/api/login")
            self.assertEqual(refresh.await_count, 1 if refresh_token else 0)


@skipUnless(importlib.util.find_spec("aiosqlite"), "install dev dependency aiosqlite")
class SsoPersistenceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Production uses PostgreSQL row locks; SQLite tests persistence and stale snapshots.
        self.temp = tempfile.TemporaryDirectory()
        key = Path(self.temp.name) / "key"
        key.write_text("test-master-key-only-never-production-123456")
        self.env = patch.dict(os.environ, {"APP_MASTER_KEY_FILE": str(key)})
        self.env.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(
                CommunicationSource(
                    id="mts",
                    label="Test source",
                    source_type="mts_link",
                    enabled=False,
                    settings={"base_url": "https://gw.mts-link.ru"},
                )
            )
            await session.commit()
        bootstrap = AppConfig(database={"url": "postgresql+asyncpg://test:test@localhost/test"})
        with patch("improver.config.get_config", return_value=bootstrap):
            from improver.api import mts_link_auth as api
            from improver.db import get_session

        self.api = api
        app = FastAPI()
        app.include_router(api.router)

        async def get_test_session():
            async with self.sessions() as session:
                yield session

        app.dependency_overrides[get_session] = get_test_session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="https://admin.test"
        )
        self.orgs = patch.object(
            api,
            "_organizations",
            AsyncMock(
                return_value=[
                    {
                        "id": "org",
                        "name": "Test",
                        "methods": [
                            {
                                "type": "SAMLLoginMethod",
                                "value": {
                                    "params": "opaque-params",
                                    "connectionToken": "connection",
                                },
                            }
                        ],
                    }
                ]
            ),
        )
        self.orgs.start()
        self.exchange = patch.object(
            api,
            "exchange_tokens",
            AsyncMock(return_value=auth.MtsTokens("first-access", "first-refresh")),
        )
        self.exchange_mock = self.exchange.start()
        self.factory = patch("improver.db.SessionFactory", self.sessions)
        self.factory.start()

    async def asyncTearDown(self):
        self.factory.stop()
        self.exchange.stop()
        self.orgs.stop()
        await self.client.aclose()
        await self.engine.dispose()
        self.env.stop()
        self.temp.cleanup()

    async def start_login(self):
        response = await self.client.post(
            "/admin/sources/mts/mts-link/start",
            json={
                "email": "user@example.test",
                "organization_id": "org",
                "method_index": 0,
                "enable_source": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def finish_login(self, start):
        return await self.client.post(
            "/admin/sources/mts/mts-link/finish",
            json={"ticket": start["ticket"], "auth_code": "test-code"},
        )

    async def test_complete_flow_encrypts_both_tokens_and_rejects_replay(self):
        choices = (
            await self.client.post(
                "/admin/sources/mts/mts-link/organizations", json={"email": "user@example.test"}
            )
        ).json()
        self.assertNotIn("connection", json.dumps(choices))
        start = await self.start_login()
        self.assertIn("returnUrl=mtslink%3A%2F%2Fmobile%2Flogin", start["authorization_url"])
        response = await self.finish_login(start)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["refresh_token_configured"])
        self.assertTrue(response.json()["enabled"])
        self.assertNotIn("first-access", response.text)
        self.assertNotIn("first-refresh", response.text)
        async with self.sessions() as session:
            row = await session.get(CommunicationSource, "mts")
            self.assertNotIn("first-", row.credential_encrypted)
            config = source_config_from_record(row)
            self.assertEqual(config.refresh_token, "first-refresh")
        self.assertEqual((await self.finish_login(start)).status_code, 409)
        self.exchange_mock.assert_awaited_once()

    async def test_expired_tampered_wrong_source_and_edited_source_tickets(self):
        start = await self.start_login()
        cipher = SecretCipher()
        for change in [{"expires": time.time() - 1}, {"source_id": "another"}]:
            ticket = json.loads(cipher.decrypt(start["ticket"]))
            ticket.update(change)
            response = await self.finish_login({"ticket": cipher.encrypt(json.dumps(ticket))})
            self.assertEqual(response.status_code, 409)
        self.assertEqual((await self.finish_login({"ticket": "invalid"})).status_code, 409)
        async with self.sessions() as session:
            row = await session.get(CommunicationSource, "mts")
            row.settings = {"base_url": "https://gw.mts-link.ru", "poll_interval_seconds": 100}
            await session.commit()
        self.assertEqual((await self.finish_login(start)).status_code, 409)
        self.exchange_mock.assert_not_awaited()

    async def test_refresh_commits_and_stale_second_caller_reuses_new_pair(self):
        await self.finish_login(await self.start_login())
        async with self.sessions() as session:
            source = source_config_from_record(await session.get(CommunicationSource, "mts"))
        refresh = AsyncMock(return_value=auth.MtsTokens("rotated-access", "rotated-refresh"))
        with patch.object(auth, "exchange_tokens", refresh):
            first = await auth.refresh_source_tokens(source, "first-access")
            second = await auth.refresh_source_tokens(source, "first-access")
        refresh.assert_awaited_once_with(
            "https://gw.mts-link.ru", "Refresh", {"refreshToken": "first-refresh"}
        )
        self.assertEqual(first, second)
        async with self.sessions() as session:
            row = await session.get(CommunicationSource, "mts")
            self.assertEqual(source_config_from_record(row).refresh_token, "rotated-refresh")

    async def test_failed_refresh_preserves_existing_pair(self):
        await self.finish_login(await self.start_login())
        async with self.sessions() as session:
            source = source_config_from_record(await session.get(CommunicationSource, "mts"))
        with patch.object(
            auth, "exchange_tokens", AsyncMock(side_effect=auth.MtsLinkAuthError("failed"))
        ):
            with self.assertRaises(auth.MtsLinkAuthError):
                await auth.refresh_source_tokens(source, "first-access")
        async with self.sessions() as session:
            row = await session.get(CommunicationSource, "mts")
            self.assertEqual(source_config_from_record(row).refresh_token, "first-refresh")

    async def test_zip_contains_installable_manifest(self):
        response = await self.client.get("/admin/mts-link/extension.zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["manifest_version"], 3)
            self.assertNotIn("host_permissions", manifest)
            self.assertIn("background.js", archive.namelist())

    async def test_login_email_uses_saved_connection_without_exposing_tokens(self):
        from improver.api import mts_link_auth as api

        await self.finish_login(await self.start_login())

        def handle(request):
            self.assertEqual(request.url.path, "/accountUcaas/AccountUcaas.GetLoginData")
            self.assertIn("Bearer ", request.headers["Authorization"])
            return httpx.Response(200, json={"type": "LoginData", "value": {"email": "work@example.test"}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(api.httpx, "AsyncClient", return_value=client):
            response = await self.client.get("/admin/sources/mts/mts-link/login-email")
        self.assertEqual(response.json(), {"email": "work@example.test"})
        self.assertEqual(response.headers["cache-control"], "no-store")

        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(401, text="private-upstream-data")))
        with patch.object(api.httpx, "AsyncClient", return_value=client):
            response = await self.client.get("/admin/sources/mts/mts-link/login-email")
        self.assertEqual(response.json(), {"email": None})
