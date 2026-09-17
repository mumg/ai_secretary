"""Exercise bundled native code on the target OS without changing services or data."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.etree.ElementTree import Element, SubElement, tostring


PYTHON_PROBE = """
import asyncpg, cryptography, lxml.etree, pydantic_core, regex
import exchangelib, firebase_admin, sqlalchemy, uvicorn
import secretary_document_parser
from cryptography.fernet import Fernet
from zoneinfo import ZoneInfo
ZoneInfo('Europe/Moscow')
key = Fernet(Fernet.generate_key())
assert key.decrypt(key.encrypt(b'probe')) == b'probe'
assert regex.fullmatch(r'INC[0-9]+', 'INC00001', timeout=1)
print('Python and native dependencies: OK')
"""


def check_runtime(root: Path) -> None:
    # WinSW 2 loads its adjacent configuration even for the "version" command.
    # Give the probe a temporary configuration and log directory, never a real service ID.
    with tempfile.TemporaryDirectory(prefix="secretary-runtime-") as temporary:
        directory = Path(temporary)
        wrapper = directory / "WinSW.exe"
        shutil.copyfile(root / "vendor/WinSW.exe", wrapper)
        config = Element("service")
        for key, value in {
            "id": "AISecretaryRuntimeProbe", "name": "Runtime probe",
            "description": "Version check only; never installed as a service",
            "executable": str(root / "python/python.exe"), "logpath": str(directory),
        }.items():
            SubElement(config, key).text = value
        wrapper.with_suffix(".xml").write_text(tostring(config, encoding="unicode"), encoding="utf-8")
        _check_runtime(root, wrapper)


def _check_runtime(root: Path, wrapper: Path) -> None:
    probes = [
        ("Python dependencies", [root / "python/python.exe", "-B", "-c", PYTHON_PROBE]),
        ("PostgreSQL", [root / "postgres/bin/postgres.exe", "--version"]),
        ("PostgreSQL initdb", [root / "postgres/bin/initdb.exe", "--version"]),
        ("PostgreSQL client", [root / "postgres/bin/psql.exe", "--version"]),
        ("PostgreSQL backup", [root / "postgres/bin/pg_dump.exe", "--version"]),
        ("WinSW", [wrapper, "version"]),
        ("Caddy", [root / "caddy/caddy.exe", "version"]),
    ]
    for name, command in probes:
        try:
            result = subprocess.run(
                [str(arg) for arg in command], cwd=root, capture_output=True,
                text=True, errors="replace", timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Не удалось запустить {name}: {exc}") from exc
        if result.returncode:
            raise RuntimeError(
                f"{name}: код {result.returncode}. "
                f"{(result.stderr or result.stdout).strip()[:1000]}"
            )
        print(f"{name}: OK", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    check_runtime(parser.parse_args().root.resolve())
