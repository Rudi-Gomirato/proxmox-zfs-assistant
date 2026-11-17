"""Install, launch and uninstall the real setup in an isolated test directory."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pve_disk_assistant import __version__


def main():
    if os.name != 'nt':
        raise SystemExit('Windows only')
    test_root = (ROOT / 'test-output' / ('installer-' + uuid.uuid4().hex)).resolve()
    application = (test_root / 'app').resolve()
    if ROOT.resolve() not in application.parents or application.parent != test_root:
        raise SystemExit('Test destination outside workspace')
    test_root.mkdir(parents=True)
    setup = ROOT / f'dist/ProxmoxZfsAssistant-{__version__}-Windows-Setup.exe'
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
    def run(argv):
        subprocess.run([str(v) for v in argv], env=env, check=True, timeout=90,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    uninstaller = application / 'unins000.exe'
    try:
        run([setup, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', '/TASKS=',
             '/DIR=' + str(application), '/LOG=' + str(test_root / 'install.log')])
        assert (application / 'licenses').is_dir()
        assert (application / '_internal').is_dir()
        screen = test_root / 'installed-app.png'
        data = test_root / 'data'
        run([application / 'ProxmoxZfsAssistant.exe', '--data-dir', data, '--screenshot', screen])
        assert screen.is_file() and (data / 'assistant.db').is_file()
    finally:
        if uninstaller.is_file():
            run([uninstaller, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                 '/LOG=' + str(test_root / 'uninstall.log')])
    assert not (application / 'ProxmoxZfsAssistant.exe').exists()
    assert (test_root / 'data/assistant.db').is_file()
    print('Installer, installed application and uninstall smoke tests OK:', test_root)


if __name__ == '__main__':
    main()
