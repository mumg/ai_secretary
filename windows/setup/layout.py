"""Platform-independent installation configuration and service definitions."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring

SERVICES = ("AISecretaryDatabase", "AISecretaryParser", "AISecretaryApi", "AISecretaryWorker", "AISecretaryProxy")


def validate_options(api_port=18000, parser_port=18080, database_port=15432, public_host="", llm_url="http://127.0.0.1:11434"):
    ports = [int(api_port), int(parser_port), int(database_port)]
    if any(not 1024 <= port <= 65535 for port in ports) or len(set(ports)) != 3:
        raise ValueError("Укажите три разных порта от 1024 до 65535.")
    host = public_host.strip().lower()
    if host and (not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host)
                 or host.endswith((".local", ".localhost", ".invalid"))):
        raise ValueError("Для HTTPS нужен публичный DNS-домен без протокола и пути.")
    if any(ord(char) < 32 or char in '\\"' for char in llm_url):
        raise ValueError("Адрес Ollama содержит недопустимые символы.")
    url = urlsplit(llm_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Адрес Ollama должен быть HTTP(S) URL без паролей и параметров.")
    return dict(api_port=ports[0], parser_port=ports[1], database_port=ports[2], public_host=host, llm_url=llm_url.rstrip("/"))


def environment(data: Path, options: dict) -> dict[str, str]:
    return {
        "DATABASE_URL": f"postgresql+asyncpg://improver@127.0.0.1:{options['database_port']}/improver",
        "DATABASE_PASSWORD_FILE": str(data / "secrets" / "database-password"),
        "APP_MASTER_KEY_FILE": str(data / "secrets" / "master-key"),
        "DATA_DIR": str(data / "data"),
        "PUBLIC_URL": f"https://{options['public_host']}" if options['public_host'] else f"http://127.0.0.1:{options['api_port']}",
        "LOCAL_WEB_ONLY": "false" if options["public_host"] else "true",
        "DOCUMENT_PARSER_URL": f"http://127.0.0.1:{options['parser_port']}",
        "OLLAMA_BASE_URL": options["llm_url"],
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
    }


def service_xml(root: Path, data: Path, options: dict, name: str) -> str:
    service = Element("service")
    def field(key, value):
        SubElement(service, key).text = str(value)
    field("id", name)
    field("name", name.replace("AISecretary", "AI Секретарь — "))
    field("description", "Компонент AI Секретаря. Данные и конфигурация находятся в ProgramData.")
    field("startmode", "Automatic")
    field("delayedAutoStart", "true")
    field("stoptimeout", "60sec")
    field("logpath", data / "logs")
    SubElement(service, "log", {"mode": "roll-by-size"})
    log = service.find("log")
    SubElement(log, "sizeThreshold").text = "10240"
    SubElement(log, "keepFiles").text = "5"
    SubElement(service, "onfailure", {"action": "restart", "delay": "10sec"})
    account = SubElement(service, "serviceaccount")
    SubElement(account, "domain").text = "NT AUTHORITY"
    SubElement(account, "user").text = "LocalService"
    SubElement(account, "password").text = ""
    field("workingdirectory", root / "backend")
    if name == "AISecretaryDatabase":
        field("executable", root / "postgres" / "bin" / "postgres.exe")
        field("startarguments", subprocess.list2cmdline(["-D", str(data / "postgres"), "-p", str(options["database_port"]), "-h", "127.0.0.1"]))
        field("stopexecutable", root / "postgres" / "bin" / "pg_ctl.exe")
        field("stoparguments", subprocess.list2cmdline(["stop", "-D", str(data / "postgres"), "-m", "fast", "-w", "-t", "45"]))
    elif name == "AISecretaryProxy":
        field("executable", root / "caddy" / "caddy.exe")
        field("arguments", subprocess.list2cmdline(["run", "--config", str(data / "Caddyfile"), "--adapter", "caddyfile"]))
        field("depend", "AISecretaryApi")
        for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME"):
            SubElement(service, "env", {"name": key, "value": str(data / "caddy")})
    else:
        field("executable", root / "python" / "python.exe")
        if name == "AISecretaryParser":
            args = ["-m", "uvicorn", "secretary_document_parser:app", "--host", "127.0.0.1", "--port", str(options["parser_port"])]
        else:
            field("depend", "AISecretaryDatabase")
            field("depend", "AISecretaryParser")
            args = ["-m", "improver.worker"] if name == "AISecretaryWorker" else ["-m", "uvicorn", "improver.main:app", "--host", "127.0.0.1", "--port", str(options["api_port"])]
        field("arguments", subprocess.list2cmdline(args))
        for key, value in environment(data, options).items():
            SubElement(service, "env", {"name": key, "value": value})
    return '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(service, encoding="unicode") + "\n"


def caddy_config(data: Path, options: dict) -> str:
    # Forward slashes are accepted by Windows Caddy and avoid Caddyfile escapes.
    ca = (data / "certificates" / "client-ca.pem").as_posix()
    return f'''{{
    admin off
}}
{options['public_host']} {{
    tls {{
        issuer acme {{
            disable_tlsalpn_challenge
        }}
        client_auth {{
            mode require_and_verify
            trust_pool file {{
                pem_file "{ca}"
            }}
        }}
    }}
    encode gzip
    header {{
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy no-referrer
    }}
    reverse_proxy 127.0.0.1:{options['api_port']}
}}
'''
