from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy import select

from improver.config import SourceConfig
from improver.models import CommunicationSource
from improver.services.settings import SecretCipher
from improver.services.source_credentials import pack_mts_tokens, unpack_credential


class MtsLinkAuthError(RuntimeError):
    """Only safe messages: upstream response bodies may contain credentials."""


@dataclass(frozen=True)
class MtsTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


async def exchange_tokens(base_url: str, method: str, body: dict[str, str]) -> MtsTokens:
    if method not in {"LoginByAuthCode", "Refresh"}:
        raise ValueError("Unsupported token operation")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/accountUcaas/AccountUcaas.{method}",
                json=body,
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            raise MtsLinkAuthError("МТС Линк отклонил вход. Выполните SSO-вход заново.")
        payload = response.json()
        value = payload.get("value") if isinstance(payload, dict) else None
        if payload.get("type") != "Tokens" or not isinstance(value, dict):
            raise ValueError("Unexpected token response")
        tokens = [value.get("accessToken"), value.get("refreshToken")]
        if not all(
            isinstance(token, str)
            and 0 < len(token) <= 32768
            and all(33 <= ord(char) <= 126 for char in token)
            for token in tokens
        ):
            raise ValueError("Invalid tokens")
        return MtsTokens(*tokens)
    except httpx.HTTPError:
        raise MtsLinkAuthError("Не удалось связаться с МТС Линк. Повторите попытку.") from None
    except (ValueError, AttributeError):
        raise MtsLinkAuthError(
            "МТС Линк не вернул пару токенов. Выполните SSO-вход заново."
        ) from None


async def refresh_source_tokens(source: SourceConfig, failed_access: str) -> MtsTokens:
    """Serialize rotation across API/worker processes and commit before resuming sync.

    A separate transaction keeps rotated tokens even if transcript processing rolls back.
    A waiter reuses the tokens committed by the previous caller instead of reusing a
    consumed refresh token. Source replacement/deletion must never resurrect old tokens.
    """
    from improver.db import SessionFactory

    async with SessionFactory() as session:
        async with session.begin():
            row = await session.scalar(
                select(CommunicationSource)
                .where(CommunicationSource.id == source.id)
                .with_for_update()
            )
            if (
                row is None
                or row.source_type != "mts_link"
                or row.settings.get("base_url", "").rstrip("/") != source.base_url
                or not row.credential_encrypted
            ):
                raise MtsLinkAuthError("Настройки источника изменились. Повторите подключение.")
            cipher = SecretCipher()
            stored = unpack_credential("mts_link", cipher.decrypt(row.credential_encrypted))
            access = stored["credential"]
            refresh = stored.get("refresh_token", "")
            if access != failed_access:
                return MtsTokens(access, refresh)
            if not refresh:
                raise MtsLinkAuthError("Выполните SSO-вход МТС Линк для обновления токенов.")
            tokens = await exchange_tokens(source.base_url, "Refresh", {"refreshToken": refresh})
            row.credential_encrypted = cipher.encrypt(
                pack_mts_tokens(tokens.access_token, tokens.refresh_token)
            )
        return tokens
