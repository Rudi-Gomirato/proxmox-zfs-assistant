"""Build a replaceable-library onedir distribution, including license files."""
from importlib import metadata
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pve_disk_assistant.transport import helper_archive


def main():
    resources = ROOT / "src/pve_disk_assistant/resources"
    resources.mkdir(exist_ok=True)
    (resources / "helper.pyz").write_bytes(helper_archive())
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
               "--name", "ProxmoxZfsAssistant", "--paths", str(ROOT / "src"),
               "--workpath", str(ROOT / "build/windows-isolated"),
               "--add-data", str(resources / "helper.pyz") + os.pathsep + "pve_disk_assistant/resources",
               str(ROOT / "launch.py")]
    build_env = os.environ.copy()
    # Do not collect DLLs from unrelated tools on the developer's PATH (e.g.
    # Poppler's icuuc.dll is incompatible with Qt's Windows ICU imports).
    system_root = Path(os.environ.get("WINDIR", "C:/Windows"))
    build_env["PATH"] = os.pathsep.join([str(system_root / "System32"), str(system_root),
                                          str(Path(sys.executable).parent), str(Path(sys.base_prefix))])
    subprocess.run(command, cwd=ROOT, env=build_env, check=True)
    destination = ROOT / "dist/ProxmoxZfsAssistant"
    for file in ("LICENSE", "README.md", "PROGETTO.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / file, destination / file)
    shutil.copytree(ROOT / "docs", destination / "docs", dirs_exist_ok=True)
    licenses = destination / "licenses"
    licenses.mkdir(exist_ok=True)
    for filename in ("LICENSE.txt", "LICENSE"):
        python_license = Path(sys.base_prefix) / filename
        if python_license.is_file():
            shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
            break
    for package in ("PySide6-Essentials", "shiboken6", "paramiko", "cryptography", "bcrypt", "PyNaCl", "cffi", "pycparser", "invoke"):
        distribution = metadata.distribution(package)
        for file in distribution.files or []:
            if any("license" in part.lower() or "copying" in part.lower() or "notice" in part.lower() for part in file.parts):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    output = licenses / package / str(file).replace("..", "_")
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, output)
    shutil.make_archive(str(ROOT / "dist/ProxmoxZfsAssistant-Windows"), "zip", destination.parent, destination.name)
    import hashlib
    path = ROOT / "dist/ProxmoxZfsAssistant-Windows.zip"
    (ROOT / "dist/SHA256SUMS.txt").write_text(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n")
    print("Distribuzione:", path)


if __name__ == "__main__":
    main()
