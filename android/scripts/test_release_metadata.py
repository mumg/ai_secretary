import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_metadata', Path(__file__).with_name('release_metadata.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'build'
        self.output.mkdir()
        (self.output / 'app-release.apk').write_bytes(b'synthetic apk')
        self.metadata = dict(applicationId='net.muratov.assistant', variantName='release',
                             elements=[dict(versionCode=12, versionName='0.5.0', filters=[], outputFile='app-release.apk')])

    def prepare(self, ref='refs/tags/android-v0.5.0-12'):
        (self.output / 'output-metadata.json').write_text(json.dumps(self.metadata))
        return module.prepare(self.output, self.root / 'dist', ref)

    def test_release_files_match_real_artifact(self):
        result = self.prepare()
        self.assertEqual(13, result['size'])
        self.assertEqual(64, len(result['sha256']))
        self.assertEqual('android-v0.5.0-12', result['tag'])
        self.assertEqual(b'synthetic apk', (self.root / 'dist/ai-secretary-0.5.0.apk').read_bytes())
        self.assertFalse((self.root / 'dist/android-update.json').exists())
        self.assertEqual(f"{result['sha256']}  ai-secretary-0.5.0.apk\n",
                         (self.root / 'dist/SHA256SUMS').read_text())

    def test_reject_wrong_tag_or_manual_branch(self):
        for ref in ['refs/tags/android-v0.4.0-10', 'refs/heads/feature']:
            with self.assertRaises(ValueError): self.prepare(ref)

    def test_reject_debug_or_different_application(self):
        self.metadata['variantName'] = 'debug'
        with self.assertRaises(ValueError): self.prepare()
        self.metadata['variantName'] = 'release'
        self.metadata['applicationId'] = 'another.package'
        with self.assertRaises(ValueError): self.prepare()

    def test_reject_path_outside_build(self):
        self.metadata['elements'][0]['outputFile'] = '../secret.apk'
        (self.root / 'secret.apk').write_bytes(b'secret')
        with self.assertRaises(ValueError): self.prepare()


if __name__ == '__main__': unittest.main()
