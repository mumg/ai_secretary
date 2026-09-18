"""Build the browser extension ZIP and stage it for backend packaging (stdlib only)."""

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path


def build(output: Path, backend_web: Path) -> None:
    source = Path(__file__).resolve().parent / "src"
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    files = {"manifest.json", "README.txt", "content.js",
             manifest["background"]["service_worker"], *manifest["icons"].values(),
             *manifest["action"]["default_icon"].values()}
    for name in files:
        if Path(name).name != name or not (source / name).is_file():
            raise ValueError(f"Missing or invalid extension asset: {name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, (source / name).read_bytes())
    packaged = backend_web / "downloads" / "ai-secretary-extension.zip"
    packaged.parent.mkdir(parents=True, exist_ok=True)
    if packaged.resolve() != output.resolve():
        shutil.copyfile(output, packaged)
    print(f"{manifest['name']} {manifest['version']}: {output}")


if __name__ == "__main__":
    # Redirected Windows output may use cp1252, which cannot represent the name
    # or a Cyrillic build path. Escape unsupported log characters only.
    sys.stdout.reconfigure(errors="backslashreplace")
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "dist/ai-secretary-extension.zip")
    parser.add_argument("--backend-web", type=Path,
                        default=root / "backend/web")
    args = parser.parse_args()
    build(args.output, args.backend_web)
