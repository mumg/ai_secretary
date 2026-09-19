"""Import GitHub signing secrets into a disposable runner keychain."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys

REQUIRED = ('MACOS_CERTIFICATE_BASE64', 'MACOS_CERTIFICATE_PASSWORD', 'APPLE_ID',
            'APPLE_TEAM_ID', 'APPLE_APP_SPECIFIC_PASSWORD')


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        # security/notarytool argv contains passwords. Never print argv, captured
        # output or CalledProcessError here, even on runners without masking.
        raise RuntimeError(f'{args[0]} {args[1]} failed (exit {result.returncode})')
    return result.stdout


def parse_identities(output):
    # find-identity without -v prints matching identities and then valid ones.
    # Deduplicate by fingerprint, and tolerate tabs/multiple spaces in output.
    found = {}
    for line in output.splitlines():
        match = re.match(r'^\s*\d+\)\s+([A-Fa-f0-9]{40})\s+"([^"\n]+)"(.*)$', line)
        if match:
            fingerprint, name, status = match.groups()
            found.setdefault(fingerprint.upper(), (name, status.strip()))
    return found


def developer_id_matches(identities, team):
    return [fingerprint for fingerprint, (name, _) in identities.items()
            if name.startswith('Developer ID Application: ') and name.endswith(f'({team})')]


def select_identity(keychain, team):
    valid = parse_identities(command('security', 'find-identity', '-v', '-p', 'codesigning', keychain))
    matches = developer_id_matches(valid, team)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError('Multiple valid Developer ID Application identities match APPLE_TEAM_ID; '
                         'export only the intended certificate and its private key into the p12')
    all_identities = parse_identities(command('security', 'find-identity', '-p', 'codesigning', keychain))
    # Only public certificate names/fingerprints and validation status are logged.
    # Never dump keychain contents, the p12, subprocess argv or passwords.
    print(f'Code-signing identities: {len(all_identities)} found, {len(valid)} valid.', flush=True)
    for fingerprint, (name, status) in all_identities.items():
        print('Certificate identity: ' + json.dumps({'name': name, 'sha1': fingerprint,
              'valid': fingerprint in valid, 'status': status}, ensure_ascii=True), flush=True)
    if not all_identities:
        raise ValueError('No code-signing certificate/private-key pair found in the imported p12. '
                         'Export Developer ID Application together with its private key from My Certificates')
    if developer_id_matches(all_identities, team):
        raise ValueError('Developer ID Application matching APPLE_TEAM_ID was imported with a private key, '
                         'but macOS does not consider it valid. Check expiration, revocation and the Apple '
                         'Developer ID intermediate certificate chain; see the identity status above')
    if any(name.startswith('Developer ID Application: ') for name, _ in all_identities.values()):
        raise ValueError('Developer ID Application was found, but its team does not match APPLE_TEAM_ID. '
                         'Use the Team ID shown in the certificate name, or export the certificate for the intended team')
    raise ValueError('Wrong certificate type: the imported p12 contains no Developer ID Application identity. '
                     'Mac Developer, Apple Development, Apple Distribution and Developer ID Installer '
                     'cannot replace Developer ID Application for this DMG')


def setup(directory):
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise ValueError('Missing Actions secrets: ' + ', '.join(missing))
    team = os.environ['APPLE_TEAM_ID'].strip()
    if not re.fullmatch(r'[A-Z0-9]{10}', team):
        raise ValueError('APPLE_TEAM_ID must contain 10 uppercase letters/digits')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    keychain = str(directory / 'signing.keychain-db')
    snapshot = directory / 'search-list.json'
    if snapshot.exists():
        raise ValueError('Signing keychain already initialized; clean up first')
    original = shlex.split(command('security', 'list-keychains', '-d', 'user'))
    snapshot.write_text(json.dumps(original))
    p12 = directory / 'certificate.p12'
    try:
        p12.write_bytes(base64.b64decode(''.join(os.environ['MACOS_CERTIFICATE_BASE64'].split()), validate=True))
        os.chmod(p12, 0o600)
        password = secrets.token_urlsafe(32)
        command('security', 'create-keychain', '-p', password, keychain)
        command('security', 'set-keychain-settings', '-lut', '21600', keychain)
        command('security', 'unlock-keychain', '-p', password, keychain)
        command('security', 'import', str(p12), '-k', keychain, '-P',
                os.environ['MACOS_CERTIFICATE_PASSWORD'], '-T', '/usr/bin/codesign')
        command('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:,codesign:',
                '-s', '-k', password, keychain)
        command('security', 'list-keychains', '-d', 'user', '-s', keychain, *original)
        identity = select_identity(keychain, team)
        profile = 'secretary-ci-notary'
        command('xcrun', 'notarytool', 'store-credentials', profile,
                '--apple-id', os.environ['APPLE_ID'], '--team-id', team,
                '--password', os.environ['APPLE_APP_SPECIFIC_PASSWORD'], '--keychain', keychain)
        exported = {'MACOS_SIGNING_IDENTITY': identity, 'MACOS_SIGNING_TEAM_ID': team,
                    'MACOS_SIGNING_KEYCHAIN': keychain, 'MACOS_NOTARY_PROFILE': profile}
        with open(os.environ['GITHUB_ENV'], 'a') as output:
            for name, value in exported.items():
                output.write(f'{name}={value}\n')
        print('Developer ID identity imported; notarization credentials validated.')
    finally:
        p12.unlink(missing_ok=True)


def cleanup(directory):
    snapshot = directory / 'search-list.json'
    if snapshot.exists():
        command('security', 'list-keychains', '-d', 'user', '-s', *json.loads(snapshot.read_text()))
        snapshot.unlink()
    keychain = directory / 'signing.keychain-db'
    if keychain.exists():
        command('security', 'delete-keychain', str(keychain))
    (directory / 'certificate.p12').unlink(missing_ok=True)


if __name__ == '__main__':
    directory = Path(os.environ['RUNNER_TEMP']) / 'secretary-signing'
    try:
        if sys.argv[1:] == ['setup']:
            setup(directory)
        elif sys.argv[1:] == ['cleanup']:
            cleanup(directory)
        else:
            raise ValueError('Usage: ci-signing.py setup|cleanup')
    except Exception as error:
        # Do not allow a traceback containing secret command arguments.
        print(f'Signing setup: {error}', file=sys.stderr)
        sys.exit(1)
