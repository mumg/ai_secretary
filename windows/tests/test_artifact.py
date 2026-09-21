import json
from pathlib import Path
import sys
import tempfile
import unittest
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artifact


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.sha = 'a' * 40
        self.version = '1.2.3'
        self.installer = self.directory / 'AI-Secretary-Setup-1.2.3-windows-x64.exe'
        self.installer.write_bytes(b'tested Windows installer')
        self.checksum = self.directory / 'SHA256SUMS'
        self.checksum.write_text(f'{hashlib.sha256(self.installer.read_bytes()).hexdigest()}  {self.installer.name}\n')
        self.manifest = self.directory / 'artifact.json'
        self.manifest.write_text(json.dumps(artifact.metadata(self.directory, self.version, self.sha)))

    def test_valid_promotion(self):
        artifact.verify(self.directory, self.version, self.sha)

    def test_other_commit_or_version_rejected(self):
        for version, sha in [('1.2.4', self.sha), (self.version, 'b' * 40)]:
            with self.assertRaises(ValueError):
                artifact.verify(self.directory, version, sha)

    def test_tampered_installer_and_checksums_rejected(self):
        self.installer.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            artifact.verify(self.directory, self.version, self.sha)
        self.checksum.write_text(f'{hashlib.sha256(self.installer.read_bytes()).hexdigest()}  {self.installer.name}\n')
        with self.assertRaises(ValueError):
            artifact.verify(self.directory, self.version, self.sha)

    def test_extra_installer_and_untested_metadata_rejected(self):
        extra = self.directory / 'unexpected.exe'
        extra.touch()
        with self.assertRaises(ValueError):
            artifact.verify(self.directory, self.version, self.sha)
        extra.unlink()
        data = json.loads(self.manifest.read_text())
        data['validation'] = 'not-tested'
        self.manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            artifact.verify(self.directory, self.version, self.sha)
