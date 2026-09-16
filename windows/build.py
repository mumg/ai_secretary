"""Build the offline Windows payload from pinned vendors and explicit source paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "windows"


def verified_download(entry: dict, destination: Path) -> Path:
    path = destination / entry["filename"]
    destination.mkdir(parents=True, exist_ok=True)
    def digest():
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    if path.exists() and digest() == entry["sha256"]:
        return path
    temporary = path.with_suffix(".download")
    try:
        with urllib.request.urlopen(entry["url"], timeout=120) as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output)
        temporary.replace(path)
        if digest() != entry["sha256"]:
            path.unlink()
            raise ValueError(f"Checksum mismatch: {entry['filename']}")
    finally:
        temporary.unlink(missing_ok=True)
    return path


def extract(archive: Path, destination: Path, prefix: str = "", allowed: tuple[str, ...] = ()):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            if not item.filename.startswith(prefix) or item.is_dir():
                continue
            relative = item.filename[len(prefix):]
            if allowed and relative.split("/")[0] not in allowed:
                continue
            path = destination / relative
            if not path.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Unsafe vendor archive path")
            path.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as content, path.open("wb") as output:
                shutil.copyfileobj(content, output)


def source_copy(destination):
    for relative in ("backend/src", "backend/migrations", "windows/setup"):
        target = destination / ("setup" if relative == "windows/setup" else relative)
        shutil.copytree(ROOT / relative, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "downloads"))
    shutil.copyfile(ROOT / "backend/alembic.ini", destination / "backend/alembic.ini")
    shutil.copyfile(ROOT / "version", destination / "version")
    shutil.copyfile(ROOT / "windows/assets/secretary.ico", destination / "secretary.ico")
    shutil.copyfile(ROOT / "version", destination / "backend/src/improver/version")
    (destination / "parser").mkdir()
    shutil.copyfile(ROOT / "document-parser/main.py", destination / "parser/secretary_document_parser.py")
    for name in ("README.md", "THIRD_PARTY.md", "vendor.json"):
        shutil.copyfile(ROOT / "windows" / name, destination / name)
    subprocess.run([sys.executable, str(ROOT / "browser-extension/build.py"), "--output", str(OUT / "extension.zip"),
                    "--backend-web", str(destination / "backend/src/improver/web")], check=True)


def build():
    if os.name != "nt" or sys.version_info[:2] != (3, 13):
        raise RuntimeError("Build the installer on Windows x64 with Python 3.13 (GitHub Actions).")
    entries = json.loads((ROOT / "windows/vendor.json").read_text(encoding="utf-8"))
    vendors = {name: verified_download(entry, OUT / "vendor") for name, entry in entries.items()}
    payload = OUT / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    source_copy(payload)
    extract(vendors["python"], payload / "python")
    extract(vendors["postgres"], payload / "postgres", "pgsql/",
            ("bin", "lib", "share", "server_license.txt", "commandlinetools_3rd_party_licenses.txt"))
    extract(vendors["caddy"], payload / "caddy")
    (payload / "vendor").mkdir()
    for name in ("winsw", "vcredist"):
        shutil.copyfile(vendors[name], payload / "vendor" / entries[name]["filename"])
    # Preserve WinSW's MIT notice with its redistributable.
    with urllib.request.urlopen("https://raw.githubusercontent.com/winsw/winsw/v2.12.0/LICENSE.txt", timeout=30) as response:
        (payload / "vendor/WinSW-LICENSE.txt").write_bytes(response.read())
    packages = payload / "python/Lib/site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile", "--target", str(packages),
                    "-r", str(ROOT / "backend/requirements.txt"), str(ROOT / "document-parser"), "tzdata==2026.4"], check=True)
    inventory = subprocess.check_output([sys.executable, "-m", "pip", "freeze", "--path", str(packages)], text=True)
    (payload / "python-packages.txt").write_text(inventory, encoding="utf-8")
    (payload / "python/python313._pth").write_text("python313.zip\n.\nLib/site-packages\n../backend/src\n../parser\n../setup\nimport site\n", encoding="utf-8")
    subprocess.run([str(payload / "python/python.exe"), "-B", "-c", "import asyncpg, cryptography, secretary_document_parser; from zoneinfo import ZoneInfo; ZoneInfo('Europe/Moscow')"], check=True)
    print("Offline payload ready:", payload)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    build()
