"""Make startup failures visible even when launched through pythonw or an EXE."""
import os
import sys
import traceback
from pathlib import Path


def error_directory(argv):
    for index, arg in enumerate(argv):
        if arg == "--data-dir" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if arg.startswith("--data-dir="):
            return Path(arg.split("=", 1)[1])
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "ProxmoxZfsAssistant"


def launch_gui():
    from .gui import main
    main()


def main():
    try:
        launch_gui()
    except Exception:
        details = traceback.format_exc()
        directory = error_directory(sys.argv[1:])
        try:
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / "startup-error.log"
            output.write_text(details, encoding="utf-8")
            message = "Avvio non riuscito. Dettagli salvati in:\n" + str(output) + "\n\nSe usi il sorgente, esegui installa.cmd con Python 3.10 o successivo."
        except OSError:
            message = "Avvio non riuscito; impossibile scrivere il log. Avvia da un terminale per leggere l'errore."
        if sys.stderr is not None:
            print(details, file=sys.stderr)
        if os.name == "nt" and "--screenshot" not in sys.argv and os.environ.get("PVE_ASSISTANT_NO_ERROR_DIALOG") != "1":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Proxmox ZFS Assistant", 0x10)
        raise SystemExit(1)
