from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from improver.api.admin import _source_read
from improver.db import get_session
from improver.models import CommunicationSource
from improver.schemas import SourceRead
from improver.services.mts_link_auth import MtsLinkAuthError, exchange_tokens
from improver.services.settings import SecretCipher, source_config_from_record
from improver.services.source_credentials import pack_mts_tokens


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(no_store)])
GATEWAY = "https://gw.mts-link.ru"


class SsoEmail(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class SsoStart(SsoEmail):
    organization_id: str = Field(min_length=1, max_length=256)
    method_index: int = Field(ge=0, le=100)
    enable_source: bool = True


class SsoFinish(BaseModel):
    ticket: SecretStr = Field(max_length=65536)
    auth_code: SecretStr = Field(max_length=32768)


def _fingerprint(row: CommunicationSource) -> str:
    # Invalidates pending/replayed logins after source edits, token rotation or login.
    return hashlib.sha256(
        json.dumps(
            [
                row.source_type,
                row.settings,
                row.credential_encrypted,
                row.enabled,
                str(row.created_at),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


async def _source(session: AsyncSession, source_id: str, *, lock: bool = False):
    query = select(CommunicationSource).where(CommunicationSource.id == source_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query.options(selectinload(CommunicationSource.tags)))
    if row is None:
        raise HTTPException(404, "Источник не найден")
    if row.source_type != "mts_link" or row.settings.get("base_url") != GATEWAY:
        raise HTTPException(422, "SSO поддерживается для шлюза https://gw.mts-link.ru")
    return row


async def _organizations(email: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(
                GATEWAY + "/ssoExternal/ExternalSSO.GetLoginOrganizationsByEmail",
                json={"email": email},
            )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("value", {}).get("items")
        if not isinstance(items, list):
            raise ValueError("Unexpected SSO organizations")
        return [item for item in items if isinstance(item, dict)]
    except (httpx.HTTPError, ValueError, AttributeError):
        raise HTTPException(502, "Не удалось получить организации SSO из МТС Линк") from None


@router.post("/sources/{source_id}/mts-link/organizations")
async def organizations(
    source_id: str, payload: SsoEmail, session: AsyncSession = Depends(get_session)
):
    await _source(session, source_id)
    choices = []
    for org in await _organizations(payload.email):
        for index, method in enumerate(org.get("methods", [])):
            if method.get("type") in {"SAMLLoginMethod", "OAuthLoginMethod"}:
                choices.append(
                    {
                        "organization_id": str(org["id"]),
                        "method_index": index,
                        "name": str(org.get("name", "Организация")),
                        "kind": "SAML" if method["type"] == "SAMLLoginMethod" else "OAuth",
                    }
                )
    return {"choices": choices}


@router.get("/sources/{source_id}/mts-link/login-email")
async def login_email(source_id: str, session: AsyncSession = Depends(get_session)):
    row = await _source(session, source_id)
    token = (source_config_from_record(row).credential or "").strip()
    if token.casefold().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return {"email": None}
    try:
        async with httpx.AsyncClient(
            timeout=10, follow_redirects=False, cookies={"access": token}
        ) as client:
            response = await client.post(
                GATEWAY + "/accountUcaas/AccountUcaas.GetLoginData",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                content=b"",
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("type") != "LoginData":
            return {"email": None}
        email = SsoEmail(email=payload["value"]["email"]).email
        return {"email": email}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return {"email": None}


@router.post("/sources/{source_id}/mts-link/start")
async def start(source_id: str, payload: SsoStart, session: AsyncSession = Depends(get_session)):
    row = await _source(session, source_id)
    org = next(
        (
            item
            for item in await _organizations(payload.email)
            if str(item.get("id")) == payload.organization_id
        ),
        None,
    )
    try:
        method = org["methods"][payload.method_index]
        value = method["value"]
        query = {
            "email": payload.email,
            "params": value["params"],
            "returnUrl": "mtslink://mobile/login",
        }
        if method["type"] == "SAMLLoginMethod":
            path = "/sso/saml/login"
            query["token"] = value["connectionToken"]
        elif method["type"] == "OAuthLoginMethod":
            path = "/sso/oauth/login"
            query["id"] = value["clientId"]
        else:
            raise ValueError("Unsupported SSO method")
        if not all(isinstance(v, str) for v in query.values()):
            raise ValueError("Invalid SSO parameters")
    except (KeyError, TypeError, IndexError, ValueError):
        raise HTTPException(422, "Способ SSO изменился. Выберите организацию заново.") from None
    ticket = SecretCipher().encrypt(
        json.dumps(
            {
                "purpose": "mts-link-sso",
                "source_id": row.id,
                "fingerprint": _fingerprint(row),
                "expires": time.time() + 900,
                "enable_source": payload.enable_source,
            }
        )
    )
    return {"ticket": ticket, "authorization_url": GATEWAY + path + "?" + urlencode(query)}


@router.post("/sources/{source_id}/mts-link/finish", response_model=SourceRead)
async def finish(source_id: str, payload: SsoFinish, session: AsyncSession = Depends(get_session)):
    cipher = SecretCipher()
    try:
        ticket = json.loads(cipher.decrypt(payload.ticket.get_secret_value()))
        valid = (
            ticket["purpose"] == "mts-link-sso"
            and ticket["source_id"] == source_id
            and ticket["expires"] > time.time()
        )
    except Exception:
        valid = False
    if not valid:
        raise HTTPException(409, "Сеанс входа истёк. Начните SSO-вход заново.")
    code = payload.auth_code.get_secret_value()
    if not code or len(code) > 32768 or any(ord(c) < 33 or ord(c) > 126 for c in code):
        raise HTTPException(422, "Некорректный код входа")
    row = await _source(session, source_id, lock=True)
    if _fingerprint(row) != ticket["fingerprint"]:
        raise HTTPException(409, "Источник изменился. Начните SSO-вход заново.")
    try:
        tokens = await exchange_tokens(GATEWAY, "LoginByAuthCode", {"authCode": code})
    except MtsLinkAuthError as exc:
        raise HTTPException(502, str(exc)) from None
    row.credential_encrypted = cipher.encrypt(
        pack_mts_tokens(tokens.access_token, tokens.refresh_token)
    )
    row.enabled = bool(ticket["enable_source"])
    row.last_error = None
    await session.commit()
    await session.refresh(row)
    await session.refresh(row, attribute_names=["tags"])
    return _source_read(row)


@router.get("/mts-link/extension.zip")
async def extension_zip():
    archive = Path(__file__).parents[1] / "web" / "downloads" / "ai-secretary-extension.zip"
    if not archive.is_file():
        raise HTTPException(503, "Архив расширения ещё не собран.")
    return FileResponse(
        archive,
        media_type="application/zip",
        filename="ai-secretary-extension.zip",
        headers={"Cache-Control": "no-store"},
    )
