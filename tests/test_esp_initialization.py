"""New ESP workflow: synthetic identities, no real disk access or server writes."""
import contextlib
import io
import json
import time
from copy import deepcopy

import pytest

from pve_disk_assistant import core, esp, node
from test_boot import rebuilt, repair_plan
from test_core import boot_inventory
from test_node import approval


def request():
    return {"action": "initialize_esp", "pool_guid": "1000", "target": "/dev/disk/by-id/ata-DEMO_NEW"}


@pytest.fixture
def replacement(monkeypatch, tmp_path):
    monkeypatch.setattr(node, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(node, "locked", lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(node, "verify_target", lambda p: None)
    monkeypatch.setattr(node, "verify_partition", lambda *a: None)
    original = boot_inventory(grub=True)
    original["disks"][-1]["wwn"] = "0x5000000000000001"
    monkeypatch.setattr(node, "collect", lambda: original)
    parent = node.prepare({"pool_guid": "1000", "old_guid": "102", "target": request()["target"]})
    parent.update(state="WAITING_RESILVER", expected_esp={"partuuid": "new-partition-2"},
                  events=[{"phase": "replace", "state": "OK", "code": 0}])
    node.save_record(parent)
    fresh = rebuilt()
    fresh["pools"][0]["scan"] = "resilvered 45G in 00:38:00 with 0 errors on synthetic-date"
    target = next(d for d in fresh["disks"] if d["serial"] == "DEMO_NEW")
    target["wwn"] = original["disks"][-1]["wwn"]
    part = target["partitions"][1]
    part.update(uuid="", fstype="")
    fresh["boot"]["esps"][part["path"]] = {"probe_code": 0, "probe": {"PART_ENTRY_UUID": part["partuuid"]}, "signatures": []}
    monkeypatch.setattr(node, "collect", lambda: fresh)
    # Substitute only the hardware binding and full raw read, not provenance,
    # plan validation, approval, journal transitions or command sequencing.
    def bind(plan):
        partuuid = plan.get("expected_esp", {}).get("partuuid")
        if partuuid and partuuid != part["partuuid"]:
            raise core.SafetyError("PARTUUID cambiato")
        plan["expected_esp"] = {**part, "path": target["by_id"] + "-part2"}
    monkeypatch.setattr(node, "bind_esp", bind)
    monkeypatch.setattr(node, "blank_esp", lambda path, size, run: {"all_zero": True, "bytes_read": size})
    monkeypatch.setattr(node, "spawn_worker", lambda *a: 1)
    return parent, fresh, target, part


@pytest.mark.parametrize("code,out", [(2, ""), (0, "PART_ENTRY_SCHEME=gpt\nPART_ENTRY_UUID=synthetic\n")])
def test_empty_probe_accepts_partition_metadata_only(code, out):
    esp.verify_empty_signatures({"code": code, "out": out}, {"code": 0, "out": '{"signatures": []}'})


@pytest.mark.parametrize("probe,wipe", [
    ({"code": 0, "out": "TYPE=vfat\n"}, {"code": 0, "out": '{"signatures": []}'}),
    ({"code": 8, "out": ""}, {"code": 0, "out": '{"signatures": []}'}),
    ({"code": 0, "out": ""}, {"code": 0, "out": '{"signatures": []}'}),
    ({"code": 2, "out": "unexpected"}, {"code": 0, "out": '{"signatures": []}'}),
    ({"code": 2, "out": "", "err": "I/O error"}, {"code": 0, "out": '{"signatures": []}'}),
    ({"code": 2, "out": ""}, {"code": 0, "out": '{}'}),
    ({"code": 2, "out": ""}, {"code": 0, "out": '{"signatures": [{"type":"ext4"}]}'}),
    ({"code": 2, "out": ""}, {"code": 1, "out": '{"signatures": []}'}),
])
def test_unsafe_or_unknown_probes_never_authorize_format(probe, wipe):
    with pytest.raises(core.SafetyError):
        esp.verify_empty_signatures(probe, wipe)


def test_full_read_rejects_unrecognized_data_outside_signature_areas():
    data = bytearray(9 * 1024 ** 2)
    assert esp.read_all_zero(io.BytesIO(data), len(data), time.monotonic() + 10)["bytes_read"] == len(data)
    data[5 * 1024 ** 2 + 123] = 1
    with pytest.raises(core.SafetyError, match="byte non nulli"):
        esp.read_all_zero(io.BytesIO(data), len(data), time.monotonic() + 10)
    with pytest.raises(core.SafetyError, match="incompleta"):
        esp.read_all_zero(io.BytesIO(b"\0"), 2, time.monotonic() + 10)
    with pytest.raises(core.SafetyError, match="scaduta"):
        esp.read_all_zero(io.BytesIO(b"\0"), 1, time.monotonic() - 1)


def test_existing_repair_never_formats_even_if_esp_has_no_signatures():
    plan = repair_plan()
    assert plan["blocks"]
    assert not any(s["argv"][:2] == ["proxmox-boot-tool", "format"] for s in plan["steps"])


def test_plan_requires_server_journal_not_client_claims(replacement):
    parent, snapshot, _, _ = replacement
    req = {**request(), "esp_initialization": {"replacement_id": parent["id"], "blank": {"all_zero": True}}}
    assert core.plan_replacement(snapshot, req)["blocks"]
    plan = node.prepare(req)
    assert not plan["blocks"]
    assert plan["replacement_id"] == parent["id"]
    assert [s["argv"][:2] for s in plan["steps"]] == [["proxmox-boot-tool", a] for a in ("format", "init", "refresh", "status")]
    assert plan["steps"][1]["argv"][-1] == "grub"


@pytest.mark.parametrize("damage", ["wwn", "serial", "partuuid", "type", "mount", "zfs", "resilver", "errors", "source", "journal", "host", "unknown"])
def test_unsafe_initialization_is_blocked(replacement, damage):
    parent, snapshot, target, part = replacement
    if damage in {"wwn", "serial"}:
        target[damage] = "different"
    elif damage == "partuuid":
        part["partuuid"] = "different"
    elif damage == "type":
        part["parttype"] = core.ZFS
    elif damage == "mount":
        part["mounts"] = ["/boot/efi"]
    elif damage == "zfs":
        snapshot["pools"][0]["state"] = "DEGRADED"
    elif damage == "resilver":
        snapshot["pools"][0]["scan"] = "resilver in progress"
    elif damage == "errors":
        core.leaves(snapshot["pools"][0])[0]["errors"] = [1, 0, 0]
    elif damage == "source":
        snapshot["boot"]["esps"]["/dev/sda2"]["files"] = []
    else:
        if damage == "journal":
            parent["events"] = []
        elif damage == "host":
            parent["host_id"] = "another-node"
        else:
            parent["state"] = "UNKNOWN"
        node.save_record(parent)
    assert node.prepare(request())["blocks"]


def test_explicit_format_confirmation_is_mandatory(replacement):
    plan = node.prepare(request())
    with pytest.raises(core.SafetyError, match="Conferma esplicita"):
        node.submit(approval(plan))
    with pytest.raises(core.SafetyError, match="Conferma esplicita"):
        node.submit({**approval(plan), "format_partuuid_confirmation": "another-esp"})
    assert node.load_record(plan["id"])["state"] == "PREVIEW"


def test_new_data_after_preview_blocks_submission(replacement, monkeypatch):
    plan = node.prepare(request())
    def fail(*args):
        raise core.SafetyError("byte non nulli")
    monkeypatch.setattr(node, "blank_esp", fail)
    with pytest.raises(core.SafetyError, match="Inventario cambiato"):
        node.submit({**approval(plan), "format_partuuid_confirmation": plan["expected_esp"]["partuuid"]})


def test_complete_guided_initialization_and_no_format_replay(replacement, monkeypatch):
    parent, snapshot, target, part = replacement
    plan = node.prepare(request())
    node.submit({**approval(plan), "format_partuuid_confirmation": part["partuuid"]})
    assert node.load_record(parent["id"])["state"] == "ESP_INITIALIZATION_LINKED"
    commands = []
    healthy = rebuilt()
    healthy["disks"][-1]["wwn"] = target["wwn"]
    def run(argv, **kwargs):
        commands.append(argv)
        if argv[:2] == ["proxmox-boot-tool", "format"]:
            # The real collector verifies FAT32/UUID/fsck before init.
            snapshot["disks"] = deepcopy(healthy["disks"])
            snapshot["boot"] = deepcopy(healthy["boot"])
        return {"code": 0, "out": "", "err": ""}
    monkeypatch.setattr(node, "run", run)
    node.perform_job(plan["id"])
    finished = node.get_status(plan["id"])
    assert finished["state"] == "BOOT_TEST_PENDING", finished.get("error")
    assert finished["formatted_uuid"] == "CCCC-1111"
    assert [c[1] for c in commands] == ["format", "init", "refresh", "status"]
    node.perform_job(plan["id"])
    assert len(commands) == 4


def test_format_failure_cannot_restart_automatically(replacement, monkeypatch):
    _, _, _, part = replacement
    plan = node.prepare(request())
    node.submit({**approval(plan), "format_partuuid_confirmation": part["partuuid"]})
    calls = []
    monkeypatch.setattr(node, "run", lambda argv, **kw: calls.append(argv) or {"code": 1, "out": "", "err": "format failed"})
    node.perform_job(plan["id"])
    assert node.load_record(plan["id"])["state"] == "FAILED"
    node.perform_job(plan["id"])
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["data", "source_mode", "unapproved"])
def test_guard_immediately_before_format_rechecks_all_preconditions(replacement, monkeypatch, kind):
    _, snapshot, _, part = replacement
    plan = node.prepare(request())
    node.submit({**approval(plan), "format_partuuid_confirmation": part["partuuid"]})
    plan = node.load_record(plan["id"])
    if kind == "data":
        def failed(*a):
            raise core.SafetyError("dati aggiunti dopo approvazione")
        monkeypatch.setattr(node, "blank_esp", failed)
    elif kind == "source_mode":
        snapshot["boot"]["status"] = "unknown mode"
    else:
        plan.pop("format_approved")
    with pytest.raises(core.SafetyError, match="annullata"):
        node.verify_boot_write(plan, plan["steps"][0])


@pytest.mark.parametrize("scan", ["none requested", "resilver canceled", "resilvered 1G with 2 errors", "scrub repaired 0B with 0 errors"])
def test_missing_failed_or_pre_replacement_scan_is_insufficient(replacement, scan):
    _, snapshot, _, _ = replacement
    snapshot["pools"][0]["scan"] = scan
    assert node.prepare(request())["blocks"]


def test_other_queued_operation_cannot_be_bypassed_by_parent_link(replacement):
    parent, _, _, part = replacement
    other = deepcopy(parent)
    other.update(id="f" * 32, state="QUEUED", action="repair_boot")
    node.save_record(other)
    plan = node.prepare(request())
    with pytest.raises(core.SafetyError, match="già in coda"):
        node.submit({**approval(plan), "format_partuuid_confirmation": part["partuuid"]})


def test_existing_systemd_mode_is_preserved(replacement):
    from pve_disk_assistant.demo import add_boot_observations
    _, snapshot, _, part = replacement
    add_boot_observations(snapshot, "systemd")
    snapshot["boot"]["esps"][part["path"]] = {"probe_code": 2, "probe": {}, "signatures": []}
    plan = node.prepare(request())
    assert not plan["blocks"]
    assert plan["steps"][1]["argv"] == ["proxmox-boot-tool", "init", request()["target"] + "-part2"]


def test_runtime_wwn_mismatch_blocks_same_serial(monkeypatch):
    target = {"by_id": "/dev/disk/by-id/wwn-example", "path": "/dev/sdz", "serial": "SYNTHETIC",
              "wwn": "expected-wwn", "size": 1000}
    monkeypatch.setattr(node.os.path, "realpath", lambda p: "/dev/sdz")
    monkeypatch.setattr(node, "run", lambda *a: {"code": 0, "out": json.dumps({"blockdevices": [
        {"serial": "SYNTHETIC", "wwn": "wrong-wwn", "size": 1000, "ro": False}]})})
    with pytest.raises(core.SafetyError):
        node.verify_target({"target": target})


def test_missing_old_uuid_stays_separate_from_new_esp_plan(replacement):
    _, snapshot, _, _ = replacement
    snapshot["boot"]["missing_uuids"] = ["DEAD-0000"]
    snapshot["boot"]["status"] += "\nWARN: missing old ESP DEAD-0000"
    plan = node.prepare(request())
    assert not plan["blocks"]
    assert plan["missing_uuids"] == ["DEAD-0000"]
    assert not any(s["argv"][:2] == ["proxmox-boot-tool", "clean"] for s in plan["steps"])
