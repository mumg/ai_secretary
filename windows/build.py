"""Build the offline Windows payload from pinned vendors and explicit source paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "windows"


def validate_version():
    version = (ROOT / "version").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"Invalid release version in version: {version!r}")
    package = json.loads((ROOT / "macos/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "macos/package-lock.json").read_text(encoding="utf-8"))
    backend = tomllib.loads((ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))
    installer = re.search(r'^\s*#define\s+AppVersion\s+"([^"]+)"',
                          (ROOT / "windows/installer.iss").read_text(encoding="utf-8"), re.MULTILINE)
    versions = {
        "macos/package.json": package.get("version"),
        "macos/package-lock.json (version)": lock.get("version"),
        'macos/package-lock.json (packages[""].version)': lock.get("packages", {}).get("", {}).get("version"),
        "backend/pyproject.toml": backend.get("project", {}).get("version"),
        "windows/installer.iss (AppVersion)": installer.group(1) if installer else None,
    }
    mismatches = [f"{name}={value!r}" for name, value in versions.items() if value != version]
    if mismatches:
        raise RuntimeError(f"Release version mismatch: version={version!r}; " + "; ".join(mismatches)
                           + ". Commit the root version and all release metadata together.")
    return version


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
    version = (ROOT / "version").read_text(encoding="utf-8").strip()
    env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "windows", "GOARCH": "amd64"}
    (destination / "setup").mkdir()
    subprocess.run(["go", "build", "-trimpath", "-ldflags", f"-s -w -X main.version={version}", "-o", str(destination / "setup/secretary-setup.exe"), "./cmd/secretary-setup"], cwd=ROOT / "windows", env=env, check=True)
    subprocess.run(["go", "build", "-trimpath", "-ldflags", f"-s -w -X main.version={version}", "-o", str(destination / "backend/improver.exe"), "./cmd/improver"], cwd=ROOT / "backend", env=env, check=True)
    modules = subprocess.check_output(["go", "list", "-m", "all"], cwd=ROOT / "backend", text=True, encoding="utf-8")
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


def desktop_copy(destination):
    validate_version()
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise RuntimeError('Install Node.js 24 and run npm ci --prefix macos')
    subprocess.run([npm, 'exec', '--', 'electron-builder', '--config', '../windows/electron-builder.cjs',
                    '--win', '--x64', '--dir', '--publish', 'never'], cwd=ROOT / 'macos', check=True,
                   env={**os.environ, 'CSC_IDENTITY_AUTO_DISCOVERY': 'false'})
    shutil.copytree(OUT / 'electron/win-unpacked', destination / 'desktop')
    if not (destination / 'desktop/AI Secretary.exe').is_file():
        raise RuntimeError('Electron executable is missing')


def build(cross=False):
    if os.name != 'nt' and not cross:
        raise RuntimeError('Build on Windows, or explicitly use --cross for packaging without Windows runtime validation.')
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11+ is required on the build host.')
    validate_version()
    entries = json.loads((ROOT / "windows/vendor.json").read_text(encoding="utf-8"))
    vendors = {name: verified_download(entry, OUT / "vendor") for name, entry in entries.items()}
    payload = OUT / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    source_copy(payload)
    desktop_copy(payload)
    extract(vendors["postgres"], payload / "postgres", "pgsql/",
            ("bin", "lib", "share", "server_license.txt", "commandlinetools_3rd_party_licenses.txt"))
    extract(vendors["caddy"], payload / "caddy")
    (payload / "vendor").mkdir()
    for name in ("winsw", "vcredist"):
        shutil.copyfile(vendors[name], payload / "vendor" / entries[name]["filename"])
    # Preserve WinSW's MIT notice with its redistributable.
    with urllib.request.urlopen("https://raw.githubusercontent.com/winsw/winsw/v2.12.0/LICENSE.txt", timeout=30) as response:
        (payload / "vendor/WinSW-LICENSE.txt").write_bytes(response.read())
    if not cross:
        subprocess.run([str(payload / "setup/secretary-setup.exe"), "check-runtime", "--root", str(payload)], check=True)
    else:
        print('Cross-build: Windows runtime/install checks have NOT been run. Run windows/smoke-test.ps1 on Windows.')
    if list(payload.rglob("*.py")) or list(payload.rglob("python*.exe")) or (payload / "python").exists():
        raise RuntimeError("Python must not be included in the Windows payload")
    print("Offline payload ready:", payload)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cross', action='store_true')
    parser.add_argument('--check-version', action='store_true', help='Validate release metadata without building')
    args = parser.parse_args()
    if args.check_version:
        print('Release versions consistent:', validate_version())
    else:
        build(args.cross)
