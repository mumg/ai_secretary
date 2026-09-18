"""Build-host checks. No Python code is shipped in the Windows payload."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('windows_build', ROOT / 'windows/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

class InstallerBuildTests(unittest.TestCase):
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
