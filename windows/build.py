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
    shutil.copytree(ROOT / "backend/web", destination / "backend/web", ignore=shutil.ignore_patterns("downloads"))
    version = (ROOT / "version").read_text().strip()
    env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "windows", "GOARCH": "amd64"}
    (destination / "setup").mkdir()
    subprocess.run(["go", "build", "-trimpath", "-ldflags", f"-s -w -X main.version={version}", "-o", str(destination / "setup/secretary-setup.exe"), "./cmd/secretary-setup"], cwd=ROOT / "windows", env=env, check=True)
    subprocess.run(["go", "build", "-trimpath", "-ldflags", f"-s -w -X main.version={version}", "-o", str(destination / "backend/improver.exe"), "./cmd/improver"], cwd=ROOT / "backend", env=env, check=True)
    modules = subprocess.check_output(["go", "list", "-m", "all"], cwd=ROOT / "backend", text=True)
    (destination / "go-modules.txt").write_text(modules, encoding="utf-8")
    shutil.copytree(ROOT / "backend/third_party", destination / "third_party")
    shutil.copytree(ROOT / "windows/third_party", destination / "third_party/windows-setup")
    shutil.copyfile(ROOT / "version", destination / "version")
    shutil.copyfile(ROOT / "windows/assets/secretary.ico", destination / "secretary.ico")
    (destination / "parser").mkdir()
    subprocess.run(["go", "build", "-trimpath", "-ldflags", f"-s -w -X main.version={version}", "-o", str(destination / "parser/document-parser.exe"), "./cmd/document-parser"], cwd=ROOT / "document-parser", env=env, check=True)
    shutil.copytree(ROOT / "document-parser/third_party", destination / "third_party/document-parser")
    for name in ("README.md", "THIRD_PARTY.md", "vendor.json"):
        shutil.copyfile(ROOT / "windows" / name, destination / name)
    subprocess.run([sys.executable, str(ROOT / "browser-extension/build.py"), "--output", str(OUT / "extension.zip"),
                    "--backend-web", str(destination / "backend/web")], check=True)


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
    extract(vendors["postgres"], payload / "postgres", "pgsql/",
            ("bin", "lib", "share", "server_license.txt", "commandlinetools_3rd_party_licenses.txt"))
    extract(vendors["caddy"], payload / "caddy")
    (payload / "vendor").mkdir()
    for name in ("winsw", "vcredist"):
        shutil.copyfile(vendors[name], payload / "vendor" / entries[name]["filename"])
    # Preserve WinSW's MIT notice with its redistributable.
    with urllib.request.urlopen("https://raw.githubusercontent.com/winsw/winsw/v2.12.0/LICENSE.txt", timeout=30) as response:
        (payload / "vendor/WinSW-LICENSE.txt").write_bytes(response.read())
    subprocess.run([str(payload / "setup/secretary-setup.exe"), "check-runtime", "--root", str(payload)], check=True)
    if list(payload.rglob("*.py")) or list(payload.rglob("python*.exe")) or (payload / "python").exists():
        raise RuntimeError("Python must not be included in the Windows payload")
    print("Offline payload ready:", payload)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    build()
