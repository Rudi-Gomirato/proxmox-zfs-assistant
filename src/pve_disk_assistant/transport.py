"""SSH transport with explicit host-key verification and a fixed RPC command."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shlex
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import paramiko
from . import __version__

REMOTE = "/usr/local/lib/pve-disk-assistant/helper.pyz"
RPC = "LC_ALL=C /usr/bin/python3 " + REMOTE


@dataclass
class Connection:
    host: str
    port: int = 22
    username: str = "root"
    key_file: str = ""
    password: str = field(default="", repr=False)
    sudo: bool = False
    read_only: bool = False


class HostVerificationRequired(Exception):
    def __init__(self, hostname, key):
        self.hostname = hostname
        self.key = key
        self.fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        super().__init__(f"Nuovo server SSH: {hostname}\n{key.get_name()}\n{self.fingerprint}")


class VerifyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        raise HostVerificationRequired(hostname, key)


def helper_archive():
    base = Path(__file__).parent
    packaged = base / "resources" / "helper.pyz"
    if packaged.exists() and not (base / "node.py").exists():
        return packaged.read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        files = {"__main__.py": "from pve_disk_assistant.node import main\nmain()\n",
                 "pve_disk_assistant/__init__.py": (base / "__init__.py").read_text(encoding="utf-8")}
        for filename in ("core.py", "node.py", "boot.py", "esp.py"):
            files["pve_disk_assistant/" + filename] = (base / filename).read_text(encoding="utf-8")
        for name, contents in files.items():
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, contents.encode())
    return output.getvalue()


INSTALL_SCRIPT = r'''
import base64, hashlib, json, os, pathlib, stat, sys, tempfile
if os.geteuid() != 0:
    raise SystemExit("Installazione consentita solo con sessione root; vedere il manuale.")
request = json.load(sys.stdin)
data = base64.b64decode(request["archive"], validate=True)
if hashlib.sha256(data).hexdigest() != request["sha256"]:
    raise SystemExit("Archivio non integro")
directory = pathlib.Path("/usr/local/lib/pve-disk-assistant")
if directory.exists() or directory.is_symlink():
    st = directory.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise SystemExit("Directory helper non sicura")
else:
    directory.mkdir(mode=0o755)
dest = directory / "helper.pyz"
if dest.is_symlink():
    raise SystemExit("Destinazione helper non sicura")
fd, name = tempfile.mkstemp(prefix="helper-", dir=directory)
try:
    with os.fdopen(fd, "wb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    os.chmod(name, 0o755)
    os.replace(name, dest)
finally:
    if os.path.exists(name): os.unlink(name)
print(json.dumps({"ok": True, "result": {"path": str(dest), "sha256": request["sha256"]}}))
'''


class SSHBackend:
    def __init__(self, connection, known_hosts):
        self.connection = connection
        self.known_hosts = Path(known_hosts)

    def trust(self, error):
        keys = paramiko.HostKeys()
        if self.known_hosts.exists():
            keys.load(str(self.known_hosts))
        keys.add(error.hostname, error.key.get_name(), error.key)
        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        keys.save(str(self.known_hosts))

    def _call(self, command, request, timeout=180):
        c = self.connection
        client = paramiko.SSHClient()
        if self.known_hosts.exists():
            client.load_host_keys(str(self.known_hosts))
        client.set_missing_host_key_policy(VerifyPolicy())
        try:
            client.connect(c.host, port=c.port, username=c.username,
                           key_filename=c.key_file or None, password=c.password or None,
                           timeout=12, auth_timeout=20, banner_timeout=20,
                           look_for_keys=True, allow_agent=True)
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            stdin.write(json.dumps(request))
            stdin.flush()
            stdin.channel.shutdown_write()
            channel = stdout.channel
            out, err, start = bytearray(), bytearray(), time.monotonic()
            while True:
                while channel.recv_ready():
                    out.extend(channel.recv(65536))
                while channel.recv_stderr_ready():
                    err.extend(channel.recv_stderr(65536))
                if len(out) > 16_000_000 or len(err) > 1_000_000:
                    raise RuntimeError("Risposta SSH troppo grande.")
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if time.monotonic() - start > timeout:
                    raise RuntimeError("Timeout SSH: esito da riconciliare dal pannello Interventi.")
                time.sleep(0.03)
            code = channel.recv_exit_status()
            text = out.decode("utf-8", errors="replace")
            try:
                response = json.loads(text)
            except ValueError as exc:
                detail = err.decode("utf-8", errors="replace") or text
                raise RuntimeError("Helper non disponibile o risposta non valida. Installare l'helper dalla connessione.\n" + detail[:2000]) from exc
            if code or not response.get("ok"):
                raise RuntimeError(response.get("error") or err.decode("utf-8", errors="replace")[:2000])
            return response["result"]
        finally:
            client.close()

    def rpc(self, request):
        command = ("sudo -n " if self.connection.sudo else "") + RPC
        if self.connection.read_only:
            command += ' --read-only'
            if request.get('op') == 'plan':
                request = {**request, 'op': 'preview'}
            if request.get('op') not in {'collect', 'version', 'list_jobs', 'preview'}:
                raise RuntimeError('Modalità sola lettura: operazione modificativa non consentita.')
        if request.get("op") not in {"collect", "version"}:
            try:
                version = self._call(command, {"op": "version"})
            except RuntimeError as exc:
                raise RuntimeError("Aggiorna l'helper dalla scheda Connessione prima di usare piani e interventi della versione " + __version__ + ".") from exc
            if version.get("version") != __version__ or version.get("schema") != 2:
                raise RuntimeError("Versione helper incompatibile. Installa / aggiorna helper sul nodo, poi ripeti l'analisi.")
        return self._call(command, request)

    def install(self):
        if self.connection.sudo:
            raise RuntimeError("Installare l'helper con una connessione root; poi è possibile usare sudo limitato.")
        archive = helper_archive()
        return self._call("LC_ALL=C /usr/bin/python3 -c " + shlex.quote(INSTALL_SCRIPT),
                          {"archive": base64.b64encode(archive).decode(), "sha256": hashlib.sha256(archive).hexdigest()})

