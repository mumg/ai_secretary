"""Build a self-contained universal macOS application and DMG (no Homebrew runtime)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

import release

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / 'macos'
OUT = ROOT / 'dist/macos'
MAGIC = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'}


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def detach_image(mount):
    # Spotlight and filesystem services can briefly hold a freshly read image.
    for attempt in range(5):
        try:
            run('hdiutil', 'detach', mount, stdout=subprocess.DEVNULL)
            return
        except subprocess.CalledProcessError as error:
            if error.returncode != 16 or attempt == 4:
                raise
            print(f'Image busy; retrying detach ({attempt + 1}/4): {mount}', flush=True)
            time.sleep(2)


def digest(file, algorithm='sha256'):
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def download(entry):
    cache = OUT / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    file = cache / entry['filename']
    algorithm = 'sha256' if 'sha256' in entry else 'sha512'
    if not file.exists() or digest(file, algorithm) != entry[algorithm]:
        temporary = file.with_suffix(file.suffix + '.download')
        run('curl', '--fail', '--location', '--retry', '3', '--output', temporary, entry['url'])
        if digest(temporary, algorithm) != entry[algorithm]:
            temporary.unlink()
            raise ValueError(f'Checksum mismatch: {file.name}')
        temporary.replace(file)
    return file


def native(file):
    # Universal static archives share the fat Mach-O magic, but contain linker
    # inputs, not loadable code. codesign cannot verify them as signed binaries.
    if file.suffix == '.a' or file.is_symlink() or not file.is_file():
        return False
    with file.open('rb') as stream:
        return stream.read(4) in MAGIC


def native_dependencies(file):
    # Apple's classic otool interprets a trailing '(Plugin)' as an archive
    # member, even when the path is passed as one argv item. Use a neutral link.
    if file.name.endswith(')'):
        with tempfile.TemporaryDirectory(prefix='secretary-otool-') as directory:
            link = Path(directory) / 'binary'
            link.symlink_to(file.resolve())
            return subprocess.check_output(['otool', '-L', str(link)], text=True)
    return subprocess.check_output(['otool', '-L', str(file)], text=True)


def validate_native_tree(root):
    count = 0
    for file in root.rglob('*'):
        if not native(file):
            continue
        architectures = subprocess.check_output(['lipo', '-archs', str(file)], text=True).split()
        if not {'arm64', 'x86_64'} <= set(architectures):
            raise ValueError(f'Not universal: {file}: {architectures}')
        dependencies = native_dependencies(file)
        for line in dependencies.splitlines():
            if not line.startswith('\t'):
                continue
            dependency = line.strip().split(' (')[0]
            if dependency.startswith('/') and not dependency.endswith(':') and not dependency.startswith(('/usr/lib/', '/System/Library/')):
                raise ValueError(f'Non-portable dependency: {file}: {dependency}')
        count += 1
    if not count:
        raise ValueError(f'No native binaries found in {root}')
    print(f'Verified {count} universal Mach-O files', flush=True)


def postgres(archive, destination):
    mount = OUT / 'postgres-mount'
    mount.mkdir(exist_ok=True)
    run('hdiutil', 'attach', archive, '-readonly', '-nobrowse', '-mountpoint', mount, stdout=subprocess.DEVNULL)
    try:
        source = mount / 'Postgres.app/Contents/Versions/17'
        shutil.copytree(source, destination, symlinks=True)
        # Optional procedural languages depend on a separately installed Python/
        # Perl/Tcl runtime and are not used by the application or its migrations.
        for file in destination.rglob('*'):
            if file.is_file() and any(name in file.name for name in ('plpython', 'plperl', 'pltcl')):
                file.unlink()
        # Keep PostgreSQL licenses and all bundled libraries, including their notices.
        resources = mount / 'Postgres.app/Contents/Resources'
        licenses = destination / 'vendor-licenses'
        licenses.mkdir()
        for file in resources.iterdir():
            if any(word in file.name.lower() for word in ('license', 'copyright', 'acknowledg')):
                if file.is_dir():
                    shutil.copytree(file, licenses / file.name)
                else:
                    shutil.copy2(file, licenses / file.name)
    finally:
        detach_image(mount)
    # A few optional Postgres.app libraries retain their original install names.
    # Relocate every reference so the runtime can live in Application Support.
    prefix = '/Applications/Postgres.app/Contents/Versions/17/'
    for file in destination.rglob('*'):
        if file.is_symlink():
            target = file.resolve()
            if not target.is_relative_to(destination.resolve()) or not target.exists():
                raise ValueError(f'Non-portable PostgreSQL symlink: {file}')
        if not native(file):
            continue
        dependencies = subprocess.check_output(['otool', '-L', str(file)], text=True)
        changes = []
        seen = set()
        for line in dependencies.splitlines():
            if not line.startswith('\t'):
                continue
            dependency = line.strip().split(' (')[0]
            if dependency.startswith(prefix) and dependency not in seen:
                seen.add(dependency)
                target = destination / dependency.removeprefix(prefix)
                if not target.exists():
                    raise ValueError(f'Missing PostgreSQL library: {dependency}')
                relative = '@loader_path/' + os.path.relpath(target, file.parent)
                if target.resolve() == file.resolve():
                    changes += ['-id', relative]
                else:
                    changes += ['-change', dependency, relative]
        if changes:
            run('install_name_tool', *changes, file, stderr=subprocess.PIPE)


def icon():
    # Reuse the project's existing artwork, without ImageMagick/Homebrew.
    assets = HERE / 'assets'
    assets.mkdir(exist_ok=True)
    iconset = OUT / 'secretary.iconset'
    iconset.mkdir(exist_ok=True)
    for size in [16, 32, 128, 256, 512]:
        for scale in [1, 2]:
            suffix = '@2x' if scale == 2 else ''
            run('sips', '-s', 'format', 'png', '-z', str(size * scale), str(size * scale),
                ROOT / 'browser-extension/src/icon128.png', '--out', iconset / f'icon_{size}x{size}{suffix}.png',
                stdout=subprocess.DEVNULL)
    run('iconutil', '-c', 'icns', iconset, '-o', assets / 'secretary.icns')


def build(payload_only=False):
    release.validate_configuration()
    if sys.platform != 'darwin':
        raise SystemExit('Build on macOS with Xcode Command Line Tools, Go, Node.js and Python 3.11+.')
    version = (ROOT / 'version').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid root version')
    if json.loads((HERE / 'package.json').read_text())['version'] != version:
        raise ValueError('macos/package.json must match root version')
    if f'version = "{version}"' not in (ROOT / 'backend/pyproject.toml').read_text() or f'AppVersion "{version}"' not in (ROOT / 'windows/installer.iss').read_text():
        raise ValueError('Backend/Windows versions must match root version')
    OUT.mkdir(parents=True, exist_ok=True)
    payload = OUT / 'payload'
    shutil.rmtree(payload, ignore_errors=True)
    (payload / 'bin').mkdir(parents=True)
    vendor = json.loads((HERE / 'vendor.json').read_text())
    postgres(download(vendor['postgres']), payload / 'postgres')
    for module, name in [('backend', 'improver'), ('document-parser', 'document-parser')]:
        slices = []
        for arch in ['arm64', 'amd64']:
            target = OUT / f'{name}-{arch}'
            run('go', 'build', '-trimpath', '-ldflags', f'-s -w -X main.version={version}',
                '-o', target, f'./cmd/{name}', cwd=ROOT / module,
                env={**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'darwin', 'GOARCH': arch})
            slices.append(target)
        run('lipo', '-create', *slices, '-output', payload / 'bin' / name)
        shutil.copytree(ROOT / module / 'third_party', payload / 'third_party' / module)
    slices = []
    for arch in ['arm64', 'amd64']:
        archive = download(vendor[f'caddy-{arch}'])
        target = OUT / f'caddy-{arch}'
        with tarfile.open(archive) as source:
            with target.open('wb') as output:
                shutil.copyfileobj(source.extractfile('caddy'), output)
            license_dir = payload / 'third_party/caddy'
            license_dir.mkdir(parents=True, exist_ok=True)
            for name in ['LICENSE', 'README.md']:
                (license_dir / name).write_bytes(source.extractfile(name).read())
        target.chmod(0o755)
        slices.append(target)
    run('lipo', '-create', *slices, '-output', payload / 'bin/caddy')
    shutil.copytree(ROOT / 'backend/web', payload / 'web', ignore=shutil.ignore_patterns('downloads', '.DS_Store'))
    run(sys.executable, ROOT / 'browser-extension/build.py', '--output', OUT / 'ai-secretary-extension.zip', '--backend-web', payload / 'web')
    shutil.copy2(HERE / 'ca.cnf', payload / 'ca.cnf')
    shutil.copy2(HERE / 'vendor.json', payload / 'third_party/vendor.json')
    # Remove finder metadata, never package a working tree, user configuration or secrets.
    for file in payload.rglob('.DS_Store'):
        file.unlink()
    validate_native_tree(payload)
    # lipo changes signatures. Sign before hashing/copying to the persistent runtime.
    for file in payload.rglob('*'):
        if native(file):
            release.sign_native(file)
    manifest = {}
    for file in sorted(payload.rglob('*')):
        if file.is_symlink():
            manifest[str(file.relative_to(payload))] = 'symlink:' + os.readlink(file)
        elif file.is_file():
            manifest[str(file.relative_to(payload))] = digest(file)
    (payload / 'files.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    fingerprint = digest(payload / 'files.json')
    (payload / 'release.json').write_text(json.dumps({'version': version, 'digest': fingerprint}, indent=2) + '\n')
    icon()
    if payload_only:
        return
    print('Packaging Electron x64 + arm64; server payload is attached only after merging.', flush=True)
    run('npm', 'exec', '--', 'electron-builder', '--config', 'electron-builder.cjs', '--mac', '--universal', '--dir', '--publish', 'never', cwd=HERE,
        env={**os.environ, 'CSC_IDENTITY_AUTO_DISCOVERY': 'false',
             'DEBUG': ','.join(filter(None, [os.environ.get('DEBUG'), 'electron-universal']))})
    package_dmg(OUT / 'mac-universal/AI Secretary.app', version)


def package_dmg(app, version):
    print('Verifying universal architectures, payload checksums and application signatures.', flush=True)
    validate_native_tree(app)
    release.verify_payload(app / 'Contents/Resources/server')
    run('codesign', '--verify', '--deep', '--strict', app)
    if release.signed():
        for file in app.rglob('*'):
            if native(file):
                release.verify_identity(file)
        release.verify_identity(app)
        # Staple the app before creating the final disk image so both remain
        # verifiable offline after dragging the app into Applications.
        archive = OUT / 'notarization-app.zip'
        archive.unlink(missing_ok=True)
        try:
            print('Creating application archive for Apple notarization.', flush=True)
            run('ditto', '-c', '-k', '--keepParent', app, archive)
            release.notarize(archive, app, OUT / 'notarization')
        finally:
            archive.unlink(missing_ok=True)
        run('spctl', '--assess', '--type', 'execute', '--verbose=2', app)
    dmg = OUT / f'AI-Secretary-{version}-mac-universal.dmg'
    staging = OUT / 'dmg-root'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    run('ditto', app, staging / app.name)
    (staging / 'Applications').symlink_to('/Applications')
    run('hdiutil', 'create', '-ov', '-format', 'UDZO', '-fs', 'HFS+', '-volname',
        f'AI Secretary {version}', '-srcfolder', staging, dmg)
    if release.signed():
        release.sign_native(dmg, disk_image=True)
        release.verify_identity(dmg)
        release.notarize(dmg, dmg, OUT / 'notarization')
        run('spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', dmg)
    run('hdiutil', 'verify', dmg)
    verify_dmg(dmg, version)
    shutil.rmtree(staging)
    (OUT / 'SHA256SUMS').write_text(f'{digest(dmg)}  {dmg.name}\n')
    print(f'Universal DMG ready: {dmg}', flush=True)


def verify_dmg(dmg, version):
    with tempfile.TemporaryDirectory(prefix='secretary-dmg-') as directory:
        mount = Path(directory)
        run('hdiutil', 'attach', dmg, '-readonly', '-nobrowse', '-mountpoint', mount, stdout=subprocess.DEVNULL)
        try:
            app = mount / 'AI Secretary.app'
            info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
            if info['CFBundleShortVersionString'] != version:
                raise ValueError('DMG contains another release version')
            if os.readlink(mount / 'Applications') != '/Applications':
                raise ValueError('Missing Applications installation shortcut')
            run('codesign', '--verify', '--deep', '--strict', app)
            release.verify_payload(app / 'Contents/Resources/server')
            if release.signed():
                release.verify_identity(app)
                run('xcrun', 'stapler', 'validate', app)
                run('spctl', '--assess', '--type', 'execute', '--verbose=2', app)
            architectures = subprocess.check_output(['lipo', '-archs', str(app / 'Contents/MacOS/AI Secretary')], text=True).split()
            if not {'arm64', 'x86_64'} <= set(architectures):
                raise ValueError('DMG application is not universal')
        finally:
            detach_image(mount)
    print('Mounted DMG: application signature, version and architectures verified', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload-only', action='store_true')
    parser.add_argument('--verify-app', type=Path)
    args = parser.parse_args()
    if args.verify_app:
        validate_native_tree(args.verify_app)
    else:
        build(args.payload_only)
