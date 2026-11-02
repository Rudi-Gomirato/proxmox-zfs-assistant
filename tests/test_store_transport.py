import io
import zipfile

from pve_disk_assistant.demo import snapshot
from pve_disk_assistant.store import Store
from pve_disk_assistant.transport import Connection, helper_archive
from pve_disk_assistant.transport import SSHBackend
from pve_disk_assistant import __version__
import pytest


def test_password_is_not_in_connection_repr():
    assert "SUPER_SECRET" not in repr(Connection("host", password="SUPER_SECRET"))


def test_helper_contains_only_standard_library_domain_and_node():
    with zipfile.ZipFile(io.BytesIO(helper_archive())) as archive:
        assert set(archive.namelist()) == {"__main__.py", "pve_disk_assistant/__init__.py", "pve_disk_assistant/core.py", "pve_disk_assistant/node.py", "pve_disk_assistant/boot.py"}
        assert "paramiko" not in archive.read("pve_disk_assistant/node.py").decode()


def test_helper_bundle_is_reproducible():
    assert helper_archive() == helper_archive()


def test_position_survives_disk_disappearance(tmp_path):
    store = Store(tmp_path)
    snap = snapshot()
    store.snapshot(snap)
    store.set_position(snap["host_id"], "DEMO_VM_A", "Vano 3")
    snap["disks"] = []
    store.snapshot(snap)
    assert store.historical_disk("DEMO-HOST", "/dev/disk/by-id/ata-DEMO_VM_A")["serial"] == "DEMO_VM_A"
    assert store.position("DEMO-HOST", "DEMO_VM_A") == "Vano 3"
    assert store.member_identity("DEMO-HOST", "2000", "201")["serial"] == "DEMO_VM_A"


def test_old_helper_cannot_execute_or_certify_new_boot_workflow(tmp_path):
    backend = SSHBackend(Connection("synthetic-host"), tmp_path / "known_hosts")
    calls = []
    def call(command, request):
        calls.append(request["op"])
        return {"version": "0.1.0", "schema": 1}
    backend._call = call
    for operation in ("plan", "submit", "status", "record_boot_test"):
        with pytest.raises(RuntimeError, match="helper incompatibile"):
            backend.rpc({"op": operation})
    assert set(calls) == {"version"}


def test_matching_helper_allows_rpc_after_version_check(tmp_path):
    backend = SSHBackend(Connection("synthetic-host"), tmp_path / "known_hosts")
    calls = []
    def call(command, request):
        calls.append(request["op"])
        return {"version": __version__, "schema": 2} if request["op"] == "version" else {"state": "PREVIEW"}
    backend._call = call
    assert backend.rpc({"op": "plan"}) == {"state": "PREVIEW"}
    assert calls == ["version", "plan"]

