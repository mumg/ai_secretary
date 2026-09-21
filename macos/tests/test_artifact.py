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
        self.dmg = self.directory / 'AI-Secretary-1.2.3-mac-universal.dmg'
        self.dmg.write_bytes(b'tested signed image')
        self.checksum = self.directory / 'SHA256SUMS'
        self.checksum.write_text(f'{hashlib.sha256(self.dmg.read_bytes()).hexdigest()}  {self.dmg.name}\n')
        self.manifest = self.directory / 'artifact.json'
        self.manifest.write_text(json.dumps(artifact.metadata(self.directory, self.version, self.sha)))

    def test_valid_promotion(self):
        artifact.verify(self.directory, self.version, self.sha)

    def test_other_commit_or_version_rejected(self):
        for version, sha in [('1.2.4', self.sha), (self.version, 'b' * 40)]:
            with self.assertRaises(ValueError):
                artifact.verify(self.directory, version, sha)

    def test_tampered_dmg_and_checksums_rejected(self):
        self.dmg.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            artifact.verify(self.directory, self.version, self.sha)
        self.checksum.write_text(f'{hashlib.sha256(self.dmg.read_bytes()).hexdigest()}  {self.dmg.name}\n')
        with self.assertRaises(ValueError):
            artifact.verify(self.directory, self.version, self.sha)

    def test_extra_image_and_unsigned_metadata_rejected(self):
        extra = self.directory / 'unexpected.dmg'
        extra.touch()
        with self.assertRaises(ValueError):
            artifact.verify(self.directory, self.version, self.sha)
        extra.unlink()
        data = json.loads(self.manifest.read_text())
        data['signing'] = 'adhoc'
        self.manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            artifact.verify(self.directory, self.version, self.sha)
