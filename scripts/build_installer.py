"""Package the complete replaceable-library Windows app in an Inno Setup EXE."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pve_disk_assistant import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', help='Path to the Inno Setup compiler')
    args = parser.parse_args()
    candidates = [args.iscc, os.environ.get('ISCC'), shutil.which('ISCC.exe')]
    for base in (os.environ.get('ProgramFiles(x86)', ''), os.environ.get('ProgramFiles', ''),
                 str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs')):
        for version in ('7', '6'):
            candidates.append(str(Path(base) / ('Inno Setup ' + version) / 'ISCC.exe'))
    compiler = next((p for p in candidates if p and Path(p).is_file()), None)
    if not compiler:
        raise SystemExit('Install Inno Setup 6 or 7, or pass --iscc with the compiler path.')
    if not (ROOT / 'dist/ProxmoxZfsAssistant/ProxmoxZfsAssistant.exe').is_file():
        raise SystemExit('Build the application first: python scripts/build_windows.py')
    subprocess.run([compiler, '/DAppVersion=' + __version__, '/DRepoRoot=' + str(ROOT),
                    str(ROOT / 'scripts/windows_installer.iss')], check=True, cwd=ROOT)
    artifacts = [ROOT / 'dist/ProxmoxZfsAssistant-Windows.zip',
                 ROOT / f'dist/ProxmoxZfsAssistant-{__version__}-Windows-Setup.exe']
    lines = [hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name for p in artifacts]
    (ROOT / 'dist/SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('Installer:', artifacts[-1])


if __name__ == '__main__':
    main()
