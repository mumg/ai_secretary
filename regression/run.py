#!/usr/bin/env python3
"""Run email extraction regressions and review them at http://127.0.0.1:8765."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from comparison import annotate
from engine import CONTRACTS, HERE, analyze, digest, llm_config, load_case, suggestions_from_result

INPUT = HERE / "input"
RESULTS = HERE / "results"
WEB = HERE / "web"
NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,120}$")


def atomic_json(path: Path, value: dict, *, create: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if create and path.exists():
            raise FileExistsError(path)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def cases(current_model: str | None = None) -> list[dict]:
    latest = {}
    current = {}
    contract_hash = hashlib.sha256(CONTRACTS.read_bytes()).hexdigest()
    engine_hash = hashlib.sha256((HERE / "engine.py").read_bytes()).hexdigest()
    for item in list_results():
        latest.setdefault(item["case"], item)
        if item["model"] == current_model and item["contracts_sha256"] == contract_hash and item["engine_sha256"] == engine_hash:
            current.setdefault((item["case"], item["input_sha256"]), item)
    items = []
    for path in sorted(INPUT.glob("*.json")):
        if not NAME.fullmatch(path.stem):
            continue
        try:
            case = load_case(path)
            items.append({"file": path.name, "id": case["id"], "subject": case["subject"],
                          "direction": case["direction"], "valid": True,
                          "latest_result": latest.get(path.stem),
                          "current_result": current.get((path.stem, digest(case)))})
        except (ValueError, json.JSONDecodeError) as exc:
            items.append({"file": path.name, "valid": False, "error": str(exc)})
    return items


def run_case(name: str, config: dict, api_key: str) -> dict:
    if config["provider"] == "openai" and not api_key:
        raise ValueError("Для запуска задайте REGRESSION_API_KEY или --api-key-file")
    if not NAME.fullmatch(name):
        raise ValueError("Недопустимое имя файла")
    path = INPUT / (name + ".json")
    if not path.is_file():
        raise FileNotFoundError(name)
    result = analyze(load_case(path), config, api_key)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    result.update({"run_id": run_id, "case_file": path.name})
    atomic_json(RESULTS / name / (run_id + ".json"), result, create=True)
    return result


def list_results() -> list[dict]:
    items = []
    for path in RESULTS.glob("*/*.json"):
        if not NAME.fullmatch(path.parent.name) or not NAME.fullmatch(path.stem):
            continue
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            items.append((result, {"case": path.parent.name, "run_id": path.stem,
                                   "subject": result["input"]["subject"], "ran_at": result["ran_at"],
                                   "model": result["model"]["model"],
                                   "input_sha256": result.get("input_sha256"),
                                   "contracts_sha256": result.get("contracts_sha256"),
                                   "engine_sha256": result.get("engine_sha256"),
                                   "review": (result.get("review") or {}).get("verdict")}))
        except (OSError, ValueError, KeyError):
            pass
    return annotate(items)


class Handler(BaseHTTPRequestHandler):
    config: dict
    api_key: str
    lock = threading.Lock()

    def log_message(self, format_string: str, *args: object) -> None:
        # Do not print message bodies or review text to the terminal.
        print("regression:", format_string % args, file=sys.stderr)

    def send_json(self, status: int, value: object) -> None:
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict:
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("Ожидается application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if length < 2 or length > 65536:
            raise ValueError("Недопустимый размер запроса")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Ожидается JSON-объект")
        return value

    def local_host(self) -> bool:
        return self.headers.get("Host", "") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self) -> None:
        if not self.local_host():
            self.send_json(403, {"error": "Доступен только локальный адрес"})
            return
        path = urlparse(self.path).path
        if path == "/api/cases":
            self.send_json(200, cases(self.config["model"]))
        elif path.startswith("/api/cases/"):
            name = path.removeprefix("/api/cases/")
            if not NAME.fullmatch(name) or not (INPUT / (name + ".json")).is_file():
                self.send_json(404, {"error": "Письмо не найдено"})
                return
            self.send_json(200, load_case(INPUT / (name + ".json")))
        elif path == "/api/results":
            self.send_json(200, list_results())
        elif path.startswith("/api/results/"):
            parts = path.split("/")
            if len(parts) != 5 or not all(NAME.fullmatch(x) for x in parts[3:]):
                self.send_json(400, {"error": "Недопустимый путь"})
                return
            file = RESULTS / parts[3] / (parts[4] + ".json")
            if not file.is_file():
                self.send_json(404, {"error": "Результат не найден"})
                return
            result = json.loads(file.read_text(encoding="utf-8"))
            result["suggestions"] = suggestions_from_result(result)
            result.pop("decisions", None)
            summary = next((item for item in list_results() if item["case"] == parts[3]
                            and item["run_id"] == parts[4]), None)
            if summary:
                result["comparison"] = {"status": summary["comparison"],
                                        "reference_run_id": summary["reference_run_id"]}
            self.send_json(200, result)
        elif path in ("/", "/style.css", "/app.js"):
            name = "index.html" if path == "/" else path[1:]
            data = (WEB / name).read_bytes()
            content_type = {"index.html": "text/html", "style.css": "text/css", "app.js": "text/javascript"}[name]
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_json(404, {"error": "Не найдено"})

    def do_POST(self) -> None:
        if not self.local_host():
            self.send_json(403, {"error": "Доступен только локальный адрес"})
            return
        origin = self.headers.get("Origin", "")
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if origin != expected:
            self.send_json(403, {"error": "Запросы разрешены только из локального интерфейса"})
            return
        path = urlparse(self.path).path
        try:
            body = self.body()
            if path == "/api/run":
                result = run_case(str(body.get("case", "")), self.config, self.api_key)
                self.send_json(200, result)
            elif path == "/api/review":
                case, run_id = str(body.get("case", "")), str(body.get("run_id", ""))
                verdict = body.get("verdict")
                reason = str(body.get("reason", "")).strip()
                if not NAME.fullmatch(case) or not NAME.fullmatch(run_id) or verdict not in ("agree", "disagree"):
                    raise ValueError("Недопустимый отзыв")
                if verdict == "disagree" and not reason:
                    raise ValueError("Укажите причину несогласия")
                file = RESULTS / case / (run_id + ".json")
                with self.lock:
                    result = json.loads(file.read_text(encoding="utf-8"))
                    if result.get("review") and (result["review"] or {}).get("scope") != "suggestions_v1":
                        result["legacy_review"] = result["review"]
                    result["review"] = {"verdict": verdict, "reason": reason,
                                        "expected_tasks": str(body.get("expected_tasks", "")).strip(),
                                        "expected_delegations": str(body.get("expected_delegations", "")).strip(),
                                        "scope": "suggestions_v1",
                                        "reviewed_at": datetime.now(timezone.utc).isoformat()}
                    atomic_json(file, result)
                self.send_json(200, result)
            else:
                self.send_json(404, {"error": "Не найдено"})
        except FileNotFoundError:
            self.send_json(404, {"error": "Файл не найден"})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(502, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="прогнать все письма и завершиться")
    parser.add_argument("--case", help="имя одного файла input без .json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--provider", choices=("openai", "ollama"))
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--api-key-file", type=Path, help="локальный файл с токеном (не хранится в результатах)")
    args = parser.parse_args()
    config = llm_config(args.config, {"provider": args.provider, "base_url": args.base_url, "model": args.model})
    api_key = args.api_key_file.read_text(encoding="utf-8").strip() if args.api_key_file else os.getenv("REGRESSION_API_KEY", "")
    if args.once:
        if config["provider"] == "openai" and not api_key:
            parser.error("для OpenAI задайте REGRESSION_API_KEY или --api-key-file")
        selected = [args.case] if args.case else [Path(item["file"]).stem for item in cases() if item["valid"]]
        failed = False
        for index, name in enumerate(selected, 1):
            try:
                result = run_case(name, config, api_key)
                suggestions = result["suggestions"]
                print(f"[{index}/{len(selected)}] {name}: задач {len(suggestions['tasks'])}, поручений {len(suggestions['delegations'])}; {result['run_id']}", flush=True)
            except Exception as exc:
                print(f"[{index}/{len(selected)}] {name}: ошибка: {exc}", file=sys.stderr, flush=True)
                failed = True
        raise SystemExit(1 if failed else 0)
    Handler.config, Handler.api_key = config, api_key
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Regression UI: http://127.0.0.1:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
