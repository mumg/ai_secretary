"""Ad-hoc sign nested code from the inside out, preserving vendor entitlements."""
import pathlib
import subprocess
import sys

app = pathlib.Path(sys.argv[1])
magic = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'}
for file in sorted(app.rglob('*'), key=lambda p: len(p.parts), reverse=True):
    if file.is_symlink() or not file.is_file():
        continue
    with file.open('rb') as stream:
        native = stream.read(4) in magic
    if native:
        if subprocess.run(['codesign', '--verify', '--strict', str(file)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            continue
        subprocess.run(['codesign', '--force', '--sign', '-', '--preserve-metadata=entitlements', str(file)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
for bundle in sorted(app.rglob('*'), key=lambda p: len(p.parts), reverse=True):
    if not bundle.is_symlink() and bundle.suffix in ('.app', '.framework'):
        subprocess.run(['codesign', '--force', '--sign', '-', '--preserve-metadata=entitlements', str(bundle)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
