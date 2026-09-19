"""Developer ID signing and notarization shared by the build and CI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def signed():
    return bool(os.environ.get('MACOS_SIGNING_IDENTITY'))


def validate_configuration():
    if os.environ.get('MACOS_REQUIRE_SIGNING') == 'true' and not signed():
        raise ValueError('Developer ID signing is required, but no identity was configured')
    if signed() and not os.environ.get('MACOS_NOTARY_PROFILE'):
        raise ValueError('Signed builds require MACOS_NOTARY_PROFILE for notarization')
    if signed() and not re.fullmatch(r'[A-Z0-9]{10}', os.environ.get('MACOS_SIGNING_TEAM_ID', '')):
        raise ValueError('Signed builds require MACOS_SIGNING_TEAM_ID')


def sign_native(file, *, disk_image=False):
    args = ['codesign', '--force', '--sign', os.environ.get('MACOS_SIGNING_IDENTITY') or '-']
    if signed():
        args += ['--timestamp']
        if not disk_image:
            args += ['--options', 'runtime']
        if os.environ.get('MACOS_SIGNING_KEYCHAIN'):
            args += ['--keychain', os.environ['MACOS_SIGNING_KEYCHAIN']]
    subprocess.run([*args, str(file)], check=True)


def verify_identity(file):
    # Apply to every Mach-O, not just the bundle: --deep does not necessarily
    # treat native executables in Resources as nested code.
    team = os.environ['MACOS_SIGNING_TEAM_ID']
    requirement = (f'anchor apple generic and certificate leaf[subject.OU] = "{team}" '
                   'and certificate leaf[field.1.2.840.113635.100.6.1.13] exists')
    subprocess.run(['codesign', '--verify', '--strict', '--all-architectures',
                    '-R', requirement, str(file)], check=True)


def verify_payload(payload):
    manifest_data = (payload / 'files.json').read_bytes()
    release = json.loads((payload / 'release.json').read_text())
    if hashlib.sha256(manifest_data).hexdigest() != release['digest']:
        raise ValueError('Payload manifest digest changed during packaging')
    expected = json.loads(manifest_data)
    actual = {}
    for file in sorted(payload.rglob('*')):
        relative = file.relative_to(payload).as_posix()
        if relative in ('files.json', 'release.json'):
            continue
        if file.is_symlink():
            actual[relative] = 'symlink:' + os.readlink(file)
        elif file.is_file():
            with file.open('rb') as stream:
                actual[relative] = hashlib.file_digest(stream, 'sha256').hexdigest()
    if expected != actual:
        changes = []
        for name in sorted(expected.keys() | actual.keys()):
            if expected.get(name) != actual.get(name):
                kind = 'added' if name not in expected else 'missing' if name not in actual else 'changed'
                changes.append(f'{kind}: {name}')
        raise ValueError(f'Payload files changed after signing and hashing ({len(changes)}): '
                         + '; '.join(changes[:20]))


def notarize(archive, staple_target, output):
    print(f'Apple notarization: submitting {archive.name}; waiting up to 30 minutes for a result.', flush=True)
    output.mkdir(parents=True, exist_ok=True)
    auth = ['--keychain-profile', os.environ['MACOS_NOTARY_PROFILE']]
    if os.environ.get('MACOS_SIGNING_KEYCHAIN'):
        auth += ['--keychain', os.environ['MACOS_SIGNING_KEYCHAIN']]
    result = subprocess.run(['xcrun', 'notarytool', 'submit', str(archive), *auth,
                             '--wait', '--timeout', '30m', '--output-format', 'json'],
                            text=True, capture_output=True)
    (output / f'{archive.suffix[1:]}-submission.json').write_text(result.stdout)
    try:
        submission = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f'notarytool failed: {result.stderr.strip()}') from None
    submission_id = submission.get('id')
    if submission_id:
        subprocess.run(['xcrun', 'notarytool', 'log', submission_id, *auth,
                        str(output / f'{archive.suffix[1:]}-log.json')], check=False)
    if result.returncode or submission.get('status') != 'Accepted':
        raise RuntimeError(f'Notarization did not succeed: {submission.get("status", "unknown")} '
                           f'(submission {submission_id}); see {output}')
    subprocess.run(['xcrun', 'stapler', 'staple', str(staple_target)], check=True)
    subprocess.run(['xcrun', 'stapler', 'validate', str(staple_target)], check=True)
