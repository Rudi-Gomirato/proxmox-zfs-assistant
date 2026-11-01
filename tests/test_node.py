import contextlib
import json
from types import SimpleNamespace
from copy import deepcopy

import pytest

from pve_disk_assistant import core, node
from test_core import inventory, request


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(node, "STATE", tmp_path)
    monkeypatch.setattr(node, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(node, "locked", lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(node, "collect", inventory)
    return tmp_path


def test_dispatch_rejects_arbitrary_commands(sandbox):
    with pytest.raises(core.SafetyError):
        node.dispatch({"op": "exec", "argv": ["zpool", "destroy", "rpool"]})


def test_record_path_refuses_traversal(sandbox):
    with pytest.raises(core.SafetyError):
        node.load_record("../../etc/passwd")


def test_plan_is_persisted_before_approval(sandbox):
    plan = node.prepare(request())
    assert node.load_record(plan["id"])["state"] == "PREVIEW"


def approval(plan):
    return {"id": plan["id"], "approval_digest": plan["approval_digest"],
            "serial_confirmation": plan["target"]["serial"], "backup_confirmed": True, "physical_confirmed": True}


@pytest.mark.parametrize("change", [
    {"serial_confirmation": "wrong"}, {"backup_confirmed": False},
    {"physical_confirmed": False}, {"approval_digest": "tampered"}])
def test_server_requires_full_approval(sandbox, change):
    plan = node.prepare(request())
    req = approval(plan)
    req.update(change)
    with pytest.raises(core.SafetyError):
        node.submit(req)
    assert node.load_record(plan["id"])["state"] == "PREVIEW"


def test_changed_target_cannot_reuse_an_approval(sandbox, monkeypatch):
    plan = node.prepare(request())
    new = inventory()
    new["disks"][3]["serial"] = "ANOTHER_DISK"
    monkeypatch.setattr(node, "collect", lambda: new)
    with pytest.raises(core.SafetyError, match="Inventario cambiato"):
        node.submit(approval(plan))


def test_expired_plan_cannot_execute(sandbox):
    plan = node.prepare(request())
    plan["created_at"] -= 601
    node.save_record(plan)
    with pytest.raises(core.SafetyError, match="scaduto"):
        node.submit(approval(plan))


def test_queued_plan_is_not_submitted_twice(sandbox, monkeypatch):
    monkeypatch.setattr(node, "spawn_worker", lambda *a: 123)
    plan = node.prepare(request())
    assert node.submit(approval(plan))["state"] == "QUEUED"
    with pytest.raises(core.SafetyError, match="già inviato"):
        node.submit(approval(plan))


def test_only_one_plan_can_be_queued(sandbox, monkeypatch):
    monkeypatch.setattr(node, "spawn_worker", lambda *a: 123)
    one, two = node.prepare(request()), node.prepare(request())
    node.submit(approval(one))
    with pytest.raises(core.SafetyError, match="già in coda"):
        node.submit(approval(two))


def test_failed_command_is_logged_and_never_retried(sandbox, monkeypatch):
    plan = node.prepare(request())
    plan["state"] = "QUEUED"
    node.save_record(plan)
    monkeypatch.setattr(node, "verify_target", lambda *a: None)
    calls = []
    monkeypatch.setattr(node, "run", lambda argv, **kw: calls.append(argv) or {"code": 1, "out": "", "err": "device busy"})
    node.perform_job(plan["id"])
    final = node.load_record(plan["id"])
    assert final["state"] == "FAILED"
    assert final["events"][0]["state"] == "FAILED"
    node.perform_job(plan["id"])
    assert len(calls) == 1


def test_timeout_is_an_unknown_outcome_not_a_retry(sandbox, monkeypatch):
    plan = node.prepare(request())
    plan["state"] = "QUEUED"
    node.save_record(plan)
    monkeypatch.setattr(node, "verify_target", lambda *a: None)
    def timeout(*a, **kw):
        raise core.SafetyError("Timeout")
    monkeypatch.setattr(node, "run", timeout)
    node.perform_job(plan["id"])
    final = node.load_record(plan["id"])
    assert final["state"] == "UNKNOWN"
    assert final["events"][0]["state"] == "STARTED"


def test_worker_rechecks_topology_before_writes(sandbox, monkeypatch):
    plan = node.prepare(request())
    plan["state"] = "QUEUED"
    node.save_record(plan)
    altered = inventory()
    core.leaves(altered["pools"][1])[0]["state"] = "FAULTED"
    monkeypatch.setattr(node, "collect", lambda: altered)
    monkeypatch.setattr(node, "run", lambda *a, **kw: pytest.fail("Must not execute a command"))
    node.perform_job(plan["id"])
    assert node.load_record(plan["id"])["state"] == "BLOCKED"


def test_root_required_before_rpc(monkeypatch):
    monkeypatch.setattr(node.os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(core.SafetyError, match="root"):
        node.state_dir()


def test_no_shell_interpretation_in_runner(monkeypatch):
    observed = []
    monkeypatch.setattr(node.shutil, "which", lambda *a, **kw: "/usr/sbin/zpool")
    class Result:
        returncode, stdout, stderr = 0, "", ""
    def fake_run(args, **kw):
        observed.append((args, kw))
        return Result()
    monkeypatch.setattr(node.subprocess, "run", fake_run)
    node.run(["zpool", "replace", "pool;touch /tmp/oops", "100", "/dev/disk/by-id/a"])
    args, kwargs = observed[0]
    assert args[2] == "pool;touch /tmp/oops"
    assert kwargs["shell"] is False


def test_collector_uses_pveversion_short_format_and_classifies(monkeypatch):
    snap = inventory()
    status = snap["raw"]["status"]["out"]
    guids = status
    for pool in snap["pools"]:
        for m in core.leaves(pool):
            guids = guids.replace(m["name"], m["guid"])
    observed = []
    def runner(args, **kw):
        observed.append(args)
        output = ""
        if args[0] == "pveversion":
            assert args == ["pveversion"]
            output = "pve-manager/8.4.1/fixture (running kernel: 6.8.12)"
        elif args[:2] == ["zpool", "status"]:
            output = guids if "-g" in args else status
        elif args[:2] == ["zpool", "get"]:
            output = "rpool\t1000\nvmdata\t2000\n"
        elif args[0] == "lsblk":
            output = json.dumps({"blockdevices": []})
        elif args[0] == "findmnt":
            output = json.dumps({"filesystems": [snap["root"]]})
        elif args[0] == "proxmox-boot-tool":
            output = snap["boot"]["status"]
        return {"code": 0, "out": output, "err": ""}
    monkeypatch.setattr(node, "run", runner)
    monkeypatch.setattr(node, "discover_disks", lambda _: snap["disks"])
    monkeypatch.setattr(node.shutil, "which", lambda name, **kw: "/usr/sbin/" + name)
    monkeypatch.setattr(node, "read_text", lambda p: "zfspool: vm-zfs\n\tpool vmdata/vm\n" if str(p).endswith("storage.cfg") else "HOST-ID")
    monkeypatch.setattr(node.os, "uname", lambda: SimpleNamespace(nodename="pve-lab"), raising=False)
    monkeypatch.setattr(node.glob, "glob", lambda _: [])
    result = node.collect()
    assert result["complete"]
    assert result["pools"][1]["role"] == "DATI_VM"
    assert core.plan_replacement(result, request())["blocks"] == []


def test_copied_gpt_must_have_same_layout_and_different_guids(monkeypatch):
    original = {"label": "gpt", "id": "disk-old", "partitions": [
        {"start": 2048, "size": 100000, "type": core.ZFS, "uuid": "partition-old"}]}
    new = deepcopy(original)
    plan = {"source": {"table": original}, "target": {"by_id": "/dev/disk/by-id/ata-NEW"}}
    monkeypatch.setattr(node, "run", lambda *a, **k: {"out": json.dumps({"partitiontable": new}), "code": 0})
    with pytest.raises(core.SafetyError):
        node.verify_copied_layout(plan)
    new["id"] = "disk-new"
    new["partitions"][0]["uuid"] = "partition-new"
    node.verify_copied_layout(plan)
    new["partitions"][0]["size"] -= 1
    with pytest.raises(core.SafetyError):
        node.verify_copied_layout(plan)


def test_monitor_requires_the_new_member_and_clean_pool(sandbox, monkeypatch):
    plan = node.prepare(request())
    plan["state"] = "MONITORING"
    node.save_record(plan)
    before = node.get_status(plan["id"])
    assert before["state"] == "MONITORING"
    rebuilt = inventory()
    pool = rebuilt["pools"][1]
    pool["state"] = pool["children"][0]["state"] = "ONLINE"
    leaf = core.leaves(pool)[1]
    leaf.update(name=plan["new_member"], guid="999", state="ONLINE")
    monkeypatch.setattr(node, "collect", lambda: rebuilt)
    assert node.get_status(plan["id"])["state"] == "COMPLETED"


def test_overlapping_led_requests_cannot_leave_a_stale_restore(sandbox, monkeypatch):
    locate = sandbox / "locate"
    locate.write_text("0")
    snap = inventory()
    snap["disks"][2]["locate_paths"] = [str(locate)]
    monkeypatch.setattr(node, "collect", lambda: snap)
    monkeypatch.setattr(node, "spawn_worker", lambda *a: 123)
    payload = {"target": snap["disks"][2]["by_id"], "seconds": 30}
    result = node.identify(payload)
    with pytest.raises(core.SafetyError, match="già attiva"):
        node.identify(payload)
    def during_wait(_):
        assert locate.read_text() == "1"
    monkeypatch.setattr(node.time, "sleep", during_wait)
    node.led_worker(result["id"])
    assert locate.read_text() == "0"

