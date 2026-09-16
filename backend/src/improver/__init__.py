"""Improver server package."""

from pathlib import Path


def _source_version() -> str:
    package = Path(__file__).resolve().parent
    # Docker/wheel carries a copy; a source checkout reads the root file directly.
    for path in (package / "version", package.parents[2] / "version"):
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return "unknown"


__version__ = _source_version()
