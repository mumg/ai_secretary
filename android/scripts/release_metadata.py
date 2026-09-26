#!/usr/bin/env python3
"""Validate the signed Android APK and prepare versioned release files."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil


def prepare(output: Path, destination: Path, ref: str) -> dict:
    metadata = json.loads((output / "output-metadata.json").read_text())
    if metadata["applicationId"] != "net.muratov.assistant" or metadata.get("variantName") != "release":
        raise ValueError("Only the release application may be published")
    elements = metadata["elements"]
    if len(elements) != 1 or elements[0].get("filters"):
        raise ValueError("Expected one universal APK")
    element = elements[0]
    code = int(element["versionCode"])
    version = element["versionName"]
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version) or code <= 0:
        raise ValueError("Invalid version")
    tag = f"android-v{version}-{code}"
    if ref not in ("refs/heads/main", f"refs/tags/{tag}"):
        raise ValueError(f"Release tag must be {tag}; manual releases must use main")
    apk = (output / element["outputFile"]).resolve()
    if apk.parent != output.resolve() or not apk.is_file() or not 0 < apk.stat().st_size <= 200_000_000:
        raise ValueError("Invalid APK output")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    destination.mkdir(parents=True, exist_ok=True)
    name = f"ai-secretary-{version}.apk"
    shutil.copyfile(apk, destination / name)
    (destination / "SHA256SUMS").write_text(f"{digest}  {name}\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"tag={tag}\nversion={version}\ncode={code}\n")
    return dict(versionCode=code, versionName=version, tag=tag, sha256=digest, size=apk.stat().st_size)


if __name__ == "__main__":
    prepare(Path("android/app/build/outputs/apk/release"), Path("dist/android"), os.environ["GITHUB_REF"])
