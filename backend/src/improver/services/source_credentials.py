from __future__ import annotations

import json

PREFIX = "mts-link-tokens:v1:"


def unpack_credential(source_type: str, value: str) -> dict[str, str]:
    """Keep legacy single-token sources readable; bundles remain inside AES-GCM."""
    if source_type != "mts_link" or not value.startswith(PREFIX):
        return {"credential": value}
    payload = json.loads(value[len(PREFIX) :])
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), str) and payload[key]
        for key in ("access_token", "refresh_token")
    ):
        raise ValueError("Invalid stored MTS Link credentials")
    return {
        "credential": payload["access_token"],
        "refresh_token": payload["refresh_token"],
    }


def pack_mts_tokens(access_token: str, refresh_token: str) -> str:
    return PREFIX + json.dumps({"access_token": access_token, "refresh_token": refresh_token})
