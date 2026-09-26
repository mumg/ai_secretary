"""Analyze exported messages with a standalone LLM contract."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import datetime
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
CONTRACTS = HERE / "llm_contracts.json"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def clean_email(body: str) -> str:
    lines = []
    boundary = re.compile(r"^(?:On .+ wrote:|От:\s.+|From:\s.+|[-_]{2,}\s*(?:Original Message|Исходное сообщение))$", re.I)
    for line in body.replace("\x00", "").splitlines():
        trimmed = line.strip()
        if trimmed == "--" or boundary.fullmatch(trimmed):
            break
        if not trimmed.startswith(">"):
            lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def load_case(path: Path) -> dict:
    item = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(item, dict):
        raise ValueError("Письмо должно быть JSON-объектом")
    for key in ("id", "direction", "author", "body", "user_identity"):
        if not item.get(key):
            raise ValueError(f"{path.name}: отсутствует {key}")
    if not isinstance(item.get("subject"), str):
        raise ValueError(f"{path.name}: subject должен быть строкой")
    if item["direction"] not in ("INCOMING", "OUTGOING"):
        raise ValueError(f"{path.name}: direction должен быть INCOMING или OUTGOING")
    if not isinstance(item["user_identity"].get("names"), list) or not isinstance(item["user_identity"].get("addresses"), list):
        raise ValueError(f"{path.name}: user_identity должен содержать names и addresses")
    if not isinstance(item.get("participants", []), list):
        raise ValueError(f"{path.name}: participants должен быть массивом")
    return item


def llm_config(config_path: Path, overrides: dict | None = None) -> dict:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    config = {"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen3:8b",
              "context_length": 16384, "temperature": 0.1, "request_timeout_seconds": 300}
    config.update(data.get("llm", {}))
    config.update({key: value for key, value in (overrides or {}).items() if value is not None})
    if config["provider"] not in ("openai", "ollama"):
        raise ValueError("Поддерживаются provider=openai и provider=ollama")
    url = urlparse(config["base_url"])
    private_ollama = False
    if config["provider"] == "ollama" and url.scheme == "http" and url.hostname:
        try:
            private_ollama = ipaddress.ip_address(url.hostname).is_private
        except ValueError:
            private_ollama = url.hostname == "localhost"
    if not (url.scheme == "https" or private_ollama) or not url.netloc or url.username or url.password:
        raise ValueError("Для удалённой LLM требуется HTTPS; HTTP разрешён для локальной Ollama")
    return config


def ask(system: str, payload: dict, schema_name: str, contracts: dict, config: dict, api_key: str) -> dict:
    base = config["base_url"].rstrip("/")
    schema = contracts["schemas"][schema_name]
    tokens = min(8192, int(config["context_length"]) // 2)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]
    if config["provider"] == "openai":
        endpoint = (base if base.endswith("/v1") else base + "/v1") + "/chat/completions"
        data = {"model": config["model"], "messages": messages, "stream": False,
                "temperature": config["temperature"], "max_tokens": tokens,
                "response_format": {"type": "json_schema", "json_schema": {"name": "response", "schema": schema}}}
    else:
        endpoint = base + "/api/chat"
        data = {"model": config["model"], "messages": messages, "stream": False, "think": False,
                "options": {"temperature": config["temperature"], "num_ctx": config["context_length"],
                            "num_predict": tokens, "num_gpu": -1}, "keep_alive": -1, "format": schema}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = request.Request(endpoint, data=json.dumps(data, ensure_ascii=False).encode(), headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=int(config["request_timeout_seconds"])) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        raise RuntimeError(f"Модель вернула HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError("Не удалось подключиться к модели") from exc
    if config["provider"] == "openai":
        choice = result["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("Ответ модели обрезан по лимиту токенов")
        content = choice["message"]["content"]
    else:
        if result.get("done_reason") == "length":
            raise RuntimeError("Ответ модели обрезан по лимиту токенов")
        content = result["message"]["content"]
    answer = json.loads(content) if isinstance(content, str) else content
    if not isinstance(answer, dict):
        raise ValueError(f"{schema_name}: модель вернула не JSON-объект")
    return answer


def legacy_suggestions(raw: dict) -> dict:
    """Display old runs as proposals without their former application statuses."""
    analysis = raw.get("analysis") or {}
    focused = raw.get("focused_tasks")
    delegation = raw.get("delegations") or {}
    tasks = (focused if focused is not None else analysis).get("tasks", [])
    return {"tasks": [{key: item.get(key, "") for key in
                       ("title", "assignee", "assignee_address", "evidence",
                        "assignment_evidence", "reason", "confidence")}
                      for item in tasks],
            "delegations": [{key: item.get(key, "") for key in
                             ("title", "assignee_name", "assignee_email", "evidence",
                              "reason", "confidence")}
                            for item in delegation.get("items", [])]}


def suggestions_from_result(result: dict) -> dict:
    if "suggestions" in result:
        return result["suggestions"]
    raw = result.get("raw") or {}
    return legacy_suggestions(raw)

def analyze(case: dict, config: dict, api_key: str) -> dict:
    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    now = datetime.now().astimezone().isoformat()
    payload = {"current_datetime": now, "timezone": case.get("timezone", "Europe/Moscow"),
               "user_identity": case["user_identity"],
               "direction": case["direction"], "subject": case["subject"],
               "author": case["author"], "participants": case.get("participants", []),
               "body": clean_email(case["body"])[:max(8000, int(config["context_length"]) * 3) // 2],
               "attachments_text": case.get("attachments_text", []),
               "previous_events_in_thread": case.get("previous_events_in_thread", []),
               "relationships": case.get("relationships", {"managers": [], "reports": []})}
    task_answer = ask(contracts["prompts"]["tasks"], payload, "TaskExtractionResult", contracts, config, api_key)
    delegation_answer = None
    if case["direction"] == "OUTGOING":
        delegation_answer = ask(contracts["prompts"]["delegations"], payload,
                                "DelegationExtractionResult", contracts, config, api_key)
    return {"input": case, "input_sha256": digest(case), "ran_at": now,
            "model": {"provider": config["provider"], "base_url": config["base_url"], "model": config["model"],
                      "temperature": config["temperature"], "context_length": config["context_length"]},
            "contracts_sha256": hashlib.sha256(CONTRACTS.read_bytes()).hexdigest(),
            "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "raw": {"tasks": task_answer, "delegations": delegation_answer},
            "suggestions": {"tasks": task_answer["items"],
                            "delegations": (delegation_answer or {}).get("items", [])},
            "review": None}
