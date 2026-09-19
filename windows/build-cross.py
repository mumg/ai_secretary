"""Build the Windows EXE on macOS with CrossOver (does not test Windows services)."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

import build


def main():
    if sys.platform != 'darwin':
        raise RuntimeError('Use windows/build.ps1 on Windows')
    cross = Path('/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin')
    if not (cross / 'wine').is_file():
        raise RuntimeError('Install CrossOver in /Applications first')
    bottle = 'AISecretaryBuild'
    prefix = Path.home() / 'Library/Application Support/CrossOver/Bottles' / bottle
    env = {**os.environ, 'CX_DEBUGMSG': '-all'}

    def run(*args):
        subprocess.run([str(arg) for arg in args], env=env, check=True)

    def wine(executable, *args):
        run(cross / 'wine', '--bottle', bottle, '--no-convert', executable, *args)

    def windows(path):
        return 'Z:' + str(Path(path).resolve()).replace('/', '\\')

    if not prefix.exists():
        run(cross / 'cxbottle', '--bottle', bottle, '--create', '--template', 'win10_64',
            '--description', 'AI Secretary Windows build tools')
    compiler = prefix / 'drive_c/InnoSetup/ISCC.exe'
    if not compiler.exists():
        entry = json.loads((build.ROOT / 'windows/vendor.json').read_text(encoding='utf-8'))['innosetup']
        installer = build.verified_download(entry, build.OUT / 'vendor')
        wine(windows(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/DIR=C:\\InnoSetup')
    build.build(cross=True)
    version = (build.ROOT / 'version').read_text(encoding='utf-8').strip()
    # electron-builder leaves resource editing to this step on macOS. Use the
    # pinned rcedit bundled by the npm lockfile; the application stays unsigned.
    wine(windows(build.ROOT / 'macos/node_modules/electron-winstaller/vendor/rcedit.exe'),
         windows(build.OUT / 'payload/desktop/AI Secretary.exe'),
         '--set-icon', windows(build.ROOT / 'windows/assets/secretary.ico'),
         '--set-file-version', version, '--set-product-version', version,
         '--set-version-string', 'ProductName', 'AI Secretary',
         '--set-version-string', 'FileDescription', 'AI Secretary',
         '--set-version-string', 'CompanyName', 'AI Secretary contributors',
         '--set-version-string', 'OriginalFilename', 'AI Secretary.exe',
         '--set-version-string', 'InternalName', 'AI Secretary')
    wine(windows(compiler), f'/DAppVersion={version}',
         f'/DPayloadDir={windows(build.OUT / "payload")}', windows(build.ROOT / 'windows/installer.iss'))
    installer = build.OUT / f'AI-Secretary-Setup-{version}-windows-x64.exe'
    with installer.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (build.OUT / 'SHA256SUMS').write_text(f'{digest}  {installer.name}\n', encoding='utf-8')
    print(f'Installer ready: {installer}\nWindows installation/services have NOT been tested.', flush=True)


if __name__ == '__main__':
    main()
