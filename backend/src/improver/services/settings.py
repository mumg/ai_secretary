from __future__ import annotations

import base64
import json
import os
from copy import deepcopy
from email.utils import getaddresses
from hashlib import sha256
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig, SourceConfig, get_config, read_secret
from improver.models import CommunicationSource, SystemSetting

RUNTIME_KEYS = {
    "analysis_filters",
    "server",
    "calendar",
    "llm",
    "worker",
    "notifications",
    "communication_sources",
    "identity",
    "document_parser",
}


def identity_addresses_from_sources(sources: list[dict[str, Any]]) -> list[str]:
    """Derive the user's mailbox addresses from source connection settings."""
    result: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for field in ("primary_smtp_address", "username"):
            value = str(source.get(field) or "").strip()
            for _, address in getaddresses([value]):
                normalized = address.strip()
                key = normalized.casefold()
                if "@" not in normalized or key in seen:
                    continue
                seen.add(key)
                result.append(normalized)
    return result


class SecretCipher:
    def __init__(self) -> None:
        secret = read_secret(os.getenv("APP_MASTER_KEY_FILE", "/run/secrets/app_master_key"))
        if not secret or len(secret) < 32:
            raise ValueError("APP master key must contain at least 32 characters")
        self._cipher = AESGCM(sha256((secret or "").encode("utf-8")).digest())

    def encrypt(self, value: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, value.encode("utf-8"), b"improver:v1")
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str) -> str:
        payload = base64.urlsafe_b64decode(value.encode("ascii"))
        plaintext = self._cipher.decrypt(payload[:12], payload[12:], b"improver:v1")
        return plaintext.decode("utf-8")


def runtime_payload(config: AppConfig) -> dict[str, Any]:
    raw = config.model_dump(mode="json")
    raw["server"].pop("data_dir", None)
    raw["server"].pop("local_web_only", None)
    raw["notifications"].pop("firebase_credentials", None)
    raw["communication_sources"].pop("items", None)
    raw["document_parser"].pop("base_url", None)
    raw["identity"].pop("addresses", None)
    return {key: raw[key] for key in RUNTIME_KEYS}


def validate_runtime_payload(payload: dict[str, Any]) -> dict[str, Any]:
    base = get_config().model_dump(mode="python")
    for key in RUNTIME_KEYS:
        if key not in payload:
            continue
        if not isinstance(payload[key], dict):
            raise ValueError(f"Section {key!r} must be an object")
        base.setdefault(key, {}).update(deepcopy(payload[key]))
    base["server"]["local_web_only"] = get_config().server.local_web_only
    return runtime_payload(AppConfig.model_validate(base))


async def load_runtime_config(session: AsyncSession) -> AppConfig:
    base = get_config().model_dump(mode="python")
    setting = await session.get(SystemSetting, 1)
    if setting:
        for key, value in setting.payload.items():
            if key in RUNTIME_KEYS and isinstance(value, dict):
                base.setdefault(key, {}).update(deepcopy(value))
        if encrypted_key := setting.payload.get("llm_api_key_encrypted"):
            base.setdefault("llm", {})["api_key"] = SecretCipher().decrypt(encrypted_key)
        if setting.firebase_credentials_encrypted:
            credentials_json = SecretCipher().decrypt(setting.firebase_credentials_encrypted)
            base.setdefault("notifications", {})["firebase_credentials"] = json.loads(
                credentials_json
            )

    source_rows = list(
        (
            await session.execute(select(CommunicationSource).order_by(CommunicationSource.label))
        ).scalars()
    )
    sources: list[dict[str, Any]] = []
    cipher = SecretCipher() if any(row.credential_encrypted for row in source_rows) else None
    for row in source_rows:
        source = {
            "id": row.id,
            "type": row.source_type,
            "enabled": row.enabled,
            **row.settings,
        }
        if row.credential_encrypted and cipher:
            source["credential"] = cipher.decrypt(row.credential_encrypted)
        sources.append(source)
    base.setdefault("communication_sources", {})["items"] = sources
    base.setdefault("identity", {})["addresses"] = identity_addresses_from_sources(sources)
    # Deployment mode is controlled by the launcher, never by stored/UI settings.
    base["server"]["local_web_only"] = get_config().server.local_web_only
    if base["server"]["local_web_only"]:
        base["server"]["public_url"] = "http://127.0.0.1:8000"
    return AppConfig.model_validate(base)


async def save_runtime_settings(
    session: AsyncSession,
    payload: dict[str, Any],
    firebase_credentials_json: str | None = None,
    llm_api_key: SecretStr | None = None,
    clear_llm_api_key: bool = False,
) -> SystemSetting:
    setting = await session.get(SystemSetting, 1)
    merged = deepcopy(setting.payload) if setting else {}
    for key, value in payload.items():
        if key in RUNTIME_KEYS and isinstance(value, dict):
            merged.setdefault(key, {}).update(deepcopy(value))
        else:
            merged[key] = deepcopy(value)
    validated = validate_runtime_payload(merged)
    api_key = llm_api_key.get_secret_value().strip() if llm_api_key else ""
    if clear_llm_api_key and api_key:
        raise ValueError("Нельзя одновременно заменить и удалить API_KEY")
    if api_key and (len(api_key) > 8192 or any(not 33 <= ord(ch) <= 126 for ch in api_key)):
        raise ValueError("API_KEY должен быть одной строкой без пробелов")
    encrypted_key = setting.payload.get("llm_api_key_encrypted") if setting else None
    if api_key:
        encrypted_key = SecretCipher().encrypt(api_key)
    if clear_llm_api_key:
        encrypted_key = None
    if encrypted_key:
        validated["llm_api_key_encrypted"] = encrypted_key
    if setting is None:
        setting = SystemSetting(id=1, payload=validated)
        session.add(setting)
    else:
        setting.payload = validated
    if firebase_credentials_json:
        parsed = json.loads(firebase_credentials_json)
        if not isinstance(parsed, dict) or not parsed.get("project_id"):
            raise ValueError("Firebase service account JSON is invalid")
        setting.firebase_credentials_encrypted = SecretCipher().encrypt(
            json.dumps(parsed, ensure_ascii=False)
        )
    await session.flush()
    return setting


def source_config_from_record(row: CommunicationSource) -> SourceConfig:
    payload = {
        "id": row.id,
        "type": row.source_type,
        "enabled": row.enabled,
        **row.settings,
    }
    if row.credential_encrypted:
        payload["credential"] = SecretCipher().decrypt(row.credential_encrypted)
    return SourceConfig.model_validate(payload)
