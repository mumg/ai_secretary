"""Bind a tested signed DMG to its commit and verify it before publication."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def metadata(directory, version, sha):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Invalid version or commit SHA')
    name = f'AI-Secretary-{version}-mac-universal.dmg'
    if sorted(p.name for p in directory.glob('*.dmg')) != [name]:
        raise ValueError('Artifact must contain exactly the expected DMG version')
    with (directory / name).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if (directory / 'SHA256SUMS').read_text().strip() != f'{digest}  {name}':
        raise ValueError('DMG checksum mismatch')
    return dict(schema=1, commit=sha, version=version, signing='developer-id-notarized', file=name, sha256=digest)


def verify(directory, version, sha):
    expected = metadata(directory, version, sha)
    if json.loads((directory / 'artifact.json').read_text()) != expected:
        raise ValueError('Artifact provenance does not match commit, version or checksum')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'verify'])
    parser.add_argument('--sha', required=True)
    args = parser.parse_args()
    directory = ROOT / 'dist/macos'
    version = (ROOT / 'version').read_text().strip()
    if args.action == 'create':
        (directory / 'artifact.json').write_text(json.dumps(metadata(directory, version, args.sha), indent=2) + '\n')
    else:
        verify(directory, version, args.sha)
    print(f'Artifact {args.action}: {version}, {args.sha}')
