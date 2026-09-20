"""The installer must package the extension independently of Windows code pages."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "browser-extension/build.py"
spec = importlib.util.spec_from_file_location("extension_build", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ExtensionBuildTests(unittest.TestCase):
    def assert_extension(self, output, backend):
        self.assertEqual(output.read_bytes(),
                         (backend / "downloads/ai-secretary-extension.zip").read_bytes())
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            manifest_bytes = archive.read("manifest.json")
            self.assertEqual(manifest_bytes, (SCRIPT.parent / "src/manifest.json").read_bytes())
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            self.assertEqual(manifest["name"], "__MSG_appName__")
            self.assertEqual(manifest["default_locale"], "en")
            self.assertTrue(all("\\" not in name for name in archive.namelist()))
            for locale, name in (("en", "AI Secretary"), ("ru", "AI Секретарь"), ("zh_CN", "AI 秘书")):
                messages = json.loads(archive.read(f"_locales/{locale}/messages.json").decode("utf-8"))
                self.assertEqual(messages["appName"]["message"], name)
                for reference in (manifest["name"], manifest["description"], manifest["action"]["default_title"]):
                    self.assertTrue(reference.startswith("__MSG_") and reference.endswith("__"))
                    self.assertTrue(messages[reference[6:-2]]["message"])
            for asset in ("i18n.js", "translations.js", "options.html", "options.js"):
                self.assertIn(asset, archive.namelist())
            for asset in (manifest["background"]["service_worker"], *manifest["icons"].values()):
                self.assertIn(asset, archive.namelist())

    def test_manifest_with_cp1252_default_file_encoding(self):
        read_text = Path.read_text

        def windows_read_text(path, encoding=None, errors=None):
            return read_text(path, encoding=encoding or "cp1252", errors=errors)

        with tempfile.TemporaryDirectory() as tmp:
            output, backend = Path(tmp) / "extension.zip", Path(tmp) / "web"
            with patch.object(Path, "read_text", windows_read_text), contextlib.redirect_stdout(io.StringIO()):
                builder.build(output, backend)
            self.assert_extension(output, backend)

    def test_cli_with_cp1252_output_and_cyrillic_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "Сборка Секретаря"
            output, backend = directory / "extension.zip", directory / "web"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output), "--backend-web", str(backend)],
                env={**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252:strict"},
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("cp1252"))
            self.assertIn(b"extension.zip", result.stdout)
            self.assert_extension(output, backend)


if __name__ == "__main__":
    unittest.main()
