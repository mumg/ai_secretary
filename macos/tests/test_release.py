import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import release

spec = importlib.util.spec_from_file_location('ci_signing', HERE / 'ci-signing.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class ReleaseTests(unittest.TestCase):
    def test_required_signing_cannot_fall_back_to_adhoc(self):
        with patch.dict(os.environ, {'MACOS_REQUIRE_SIGNING': 'true'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'no identity'):
                release.validate_configuration()
        with patch.dict(os.environ, {'MACOS_SIGNING_IDENTITY': 'ID'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'MACOS_NOTARY_PROFILE'):
                release.validate_configuration()

    def test_native_signature_enables_runtime_and_timestamp_but_dmg_has_no_runtime(self):
        with patch.dict(os.environ, {'MACOS_SIGNING_IDENTITY': 'ID'}, clear=True), patch.object(release.subprocess, 'run') as run:
            release.sign_native(Path('server'))
            self.assertIn('--timestamp', run.call_args.args[0])
            self.assertIn('runtime', run.call_args.args[0])
            release.sign_native(Path('image.dmg'), disk_image=True)
            self.assertIn('--timestamp', run.call_args.args[0])
            self.assertNotIn('runtime', run.call_args.args[0])

    def test_packaging_detects_resigned_added_and_modified_payload_files(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory)
            binary = payload / 'server'
            binary.write_bytes(b'signed binary')
            (payload / 'alias').symlink_to('server')
            manifest = {'server': hashlib.sha256(binary.read_bytes()).hexdigest(), 'alias': 'symlink:server'}
            data = json.dumps(manifest).encode()
            (payload / 'files.json').write_bytes(data)
            (payload / 'release.json').write_text(json.dumps({'digest': hashlib.sha256(data).hexdigest()}))
            release.verify_payload(payload)
            binary.write_bytes(b'new signature timestamp')
            with self.assertRaisesRegex(ValueError, 'files changed'):
                release.verify_payload(payload)
            binary.write_bytes(b'signed binary')
            (payload / 'extra').write_text('unexpected')
            with self.assertRaisesRegex(ValueError, 'files changed'):
                release.verify_payload(payload)

    def test_notary_rejection_or_timeout_never_staples(self):
        for status, code in [('Invalid', 0), ('In Progress', 0), ('Accepted', 1)]:
            with self.subTest(status=status, code=code), tempfile.TemporaryDirectory() as directory:
                result = subprocess.CompletedProcess([], code, json.dumps({'id': 'submission', 'status': status}), '')
                with patch.dict(os.environ, {'MACOS_NOTARY_PROFILE': 'profile'}, clear=True), patch.object(release.subprocess, 'run', return_value=result) as run:
                    with self.assertRaisesRegex(RuntimeError, 'did not succeed'):
                        release.notarize(Path('app.zip'), Path('app.app'), Path(directory))
                    self.assertFalse(any('stapler' in call.args[0] for call in run.call_args_list))

    def test_accepted_submission_is_stapled_and_validated(self):
        result = subprocess.CompletedProcess([], 0, '{"id":"submission","status":"Accepted"}', '')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'MACOS_NOTARY_PROFILE': 'profile'}, clear=True), patch.object(release.subprocess, 'run', return_value=result) as run:
            release.notarize(Path('app.zip'), Path('app.app'), Path(directory))
            self.assertEqual(run.call_args_list[-2].args[0], ['xcrun', 'stapler', 'staple', 'app.app'])
            self.assertEqual(run.call_args_list[-1].args[0], ['xcrun', 'stapler', 'validate', 'app.app'])


class CredentialTests(unittest.TestCase):
    def test_missing_secrets_fail_before_keychain_access(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(ci, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'Missing Actions secrets'):
                ci.setup(Path('/unused'))
            command.assert_not_called()

    def test_command_failure_does_not_expose_password_or_tool_output(self):
        result = subprocess.CompletedProcess([], 1, 'secret stdout', 'secret stderr')
        with patch.object(ci.subprocess, 'run', return_value=result):
            with self.assertRaises(RuntimeError) as error:
                ci.command('security', 'import', '-P', 'secret password')
            self.assertEqual(str(error.exception), 'security import failed (exit 1)')

    def test_import_selects_matching_developer_id_and_removes_p12(self):
        for matching in (True, False):
            with self.subTest(matching=matching), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                environment = {key: 'test-secret' for key in ci.REQUIRED}
                environment.update(MACOS_CERTIFICATE_BASE64=base64.b64encode(b'p12').decode(),
                                   APPLE_TEAM_ID='ABCDEFGHIJ', GITHUB_ENV=str(root / 'env'))
                def command(*args):
                    if args[1] == 'list-keychains' and '-s' not in args:
                        return '"/original/login.keychain-db"'
                    if args[1] == 'find-identity':
                        team = 'ABCDEFGHIJ' if matching else 'OTHERTEAM1'
                        return f'  1) {"A" * 40} "Developer ID Application: Name ({team})"'
                    return ''
                with patch.dict(os.environ, environment, clear=True), patch.object(ci, 'command', side_effect=command):
                    if matching:
                        ci.setup(root / 'signing')
                        exported = (root / 'env').read_text()
                        self.assertIn('MACOS_SIGNING_IDENTITY=' + 'A' * 40, exported)
                        self.assertNotIn('test-secret', exported)
                    else:
                        with self.assertRaisesRegex(ValueError, 'matching APPLE_TEAM_ID'):
                            ci.setup(root / 'signing')
                        self.assertFalse((root / 'env').exists())
                    self.assertFalse((root / 'signing/certificate.p12').exists())
                    ci.cleanup(root / 'signing')
                    self.assertFalse((root / 'signing/search-list.json').exists())


if __name__ == '__main__':
    unittest.main()
