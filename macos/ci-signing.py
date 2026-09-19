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
        identities = command('security', 'find-identity', '-v', '-p', 'codesigning', keychain)
        matches = re.findall(r'\b([A-Fa-f0-9]{40}) "Developer ID Application: [^"\n]+ \('
                             + re.escape(team) + r'\)"', identities)
        if len(matches) != 1:
            raise ValueError('The p12 must contain exactly one valid Developer ID Application identity matching APPLE_TEAM_ID')
        profile = 'secretary-ci-notary'
        command('xcrun', 'notarytool', 'store-credentials', profile,
                '--apple-id', os.environ['APPLE_ID'], '--team-id', team,
                '--password', os.environ['APPLE_APP_SPECIFIC_PASSWORD'], '--keychain', keychain)
        exported = {'MACOS_SIGNING_IDENTITY': matches[0], 'MACOS_SIGNING_TEAM_ID': team,
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
