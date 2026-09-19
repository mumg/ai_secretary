"""Build-host checks. No Python code is shipped in the Windows payload."""
import importlib.util
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('windows_build', ROOT / 'windows/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

class InstallerBuildTests(unittest.TestCase):
    def test_version_mismatch_names_root_and_affected_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('version', 'macos/package.json', 'macos/package-lock.json',
                         'backend/pyproject.toml', 'windows/installer.iss'):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            (root / 'version').write_text('0.0.0\n', encoding='utf-8')
            with patch.object(builder, 'ROOT', root):
                with self.assertRaises(RuntimeError) as error:
                    builder.validate_version()
            message = str(error.exception)
            self.assertIn("version='0.0.0'", message)
            for name in ('macos/package.json', 'macos/package-lock.json', 'backend/pyproject.toml', 'windows/installer.iss'):
                self.assertIn(name, message)

    def test_version_validation_precedes_downloads_and_payload_changes(self):
        with patch.object(builder, 'validate_version', side_effect=RuntimeError('mismatch')), \
                patch.object(builder, 'verified_download') as download, \
                patch.object(builder.shutil, 'rmtree') as remove:
            with self.assertRaisesRegex(RuntimeError, 'mismatch'):
                builder.build(cross=True)
            download.assert_not_called()
            remove.assert_not_called()

    def test_desktop_package_with_cp1252_default_file_encoding(self):
        read_text = Path.read_text

        def windows_read_text(path, encoding=None, errors=None):
            return read_text(path, encoding=encoding or 'cp1252', errors=errors)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'Сборка'
            desktop = output / 'electron/win-unpacked'
            desktop.mkdir(parents=True)
            (desktop / 'AI Secretary.exe').write_bytes(b'electron-test-executable')
            payload = output / 'payload'
            with patch.object(Path, 'read_text', windows_read_text), \
                    patch.object(builder, 'OUT', output), \
                    patch.object(builder.shutil, 'which', return_value='npm.cmd'), \
                    patch.object(builder.subprocess, 'run') as run:
                builder.desktop_copy(payload)
            run.assert_called_once()
            self.assertIn('electron-builder', run.call_args.args[0])
            self.assertEqual((payload / 'desktop/AI Secretary.exe').read_bytes(), b'electron-test-executable')

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as file:
                file.writestr('../outside.txt', 'bad')
            with self.assertRaises(ValueError):
                builder.extract(archive, Path(tmp) / 'output')
            self.assertFalse((Path(tmp) / 'outside.txt').exists())

    def test_download_hash_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.write_bytes(b'corrupt download')
            with self.assertRaises(ValueError):
                builder.verified_download(dict(filename='runtime.zip', url=source.as_uri(), sha256='0'*64), Path(tmp) / 'cache')
            self.assertFalse((Path(tmp) / 'cache/runtime.zip').exists())

    def test_payload_contains_native_helper_and_no_python(self):
        original_run = subprocess.run
        compiles = []
        def run(command, **kwargs):
            if command[:2] == ['go', 'build']:
                compiles.append(command)
                output = Path(command[command.index('-o') + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b'native-test-executable')
                return subprocess.CompletedProcess(command, 0)
            return original_run(command, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / 'payload'
            payload.mkdir()
            with patch.object(builder, 'OUT', Path(tmp)), patch.object(builder.subprocess, 'run', side_effect=run), patch.object(builder.subprocess, 'check_output', return_value='module test\n'):
                builder.source_copy(payload)
            self.assertTrue((payload / 'setup/secretary-setup.exe').is_file())
            self.assertTrue((payload / 'backend/improver.exe').is_file())
            self.assertTrue((payload / 'parser/document-parser.exe').is_file())
            self.assertTrue((payload / 'backend/web/downloads/ai-secretary-extension.zip').is_file())
            self.assertTrue((payload / 'third_party/windows-setup/go-modules.txt').is_file())
            self.assertEqual(len(compiles), 3)
            self.assertFalse(list(payload.rglob('*.py')))
            self.assertFalse((payload / 'python').exists())
        self.assertNotIn('python', json.loads((ROOT / 'windows/vendor.json').read_text()))

if __name__ == '__main__':
    unittest.main()
