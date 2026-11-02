"""Synthetic regressions for ONLINE mirrors with missing or broken boot paths.

No real serials, UUIDs, addresses or logs from the shared incident are retained.
"""
import contextlib
import json
import stat
import struct
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from pve_disk_assistant import boot, core, demo, node
from test_core import inventory, boot_inventory


def defective_efi():
    snapshot = demo.snapshot(boot_fault=True)
    snapshot.pop("demo")
    return snapshot


def rebuilt():
    snapshot = inventory()
    original = snapshot["disks"][1]
    target = next(d for d in snapshot["disks"] if d["serial"] == "DEMO_NEW")
    target["partitions"] = deepcopy(original["partitions"])
    target["table"] = {"label": "gpt"}
    target["signatures"] = [{"type": "gpt"}]
    for part in target["partitions"]:
        part["path"] = target["path"] + str(part["number"])
        part["aliases"] = [target["by_id"] + "-part" + str(part["number"])]
        part["partuuid"] = "new-partition-" + str(part["number"])
        if part["number"] == 2:
            part["uuid"] = "CCCC-1111"
    snapshot["disks"].remove(original)
    member = core.leaves(snapshot["pools"][0])[1]
    member.update(name=target["by_id"] + "-part3", guid="999", state="ONLINE")
    demo.add_boot_observations(snapshot, "grub")
    return snapshot


def repair_plan(snapshot=None):
    snapshot = snapshot or defective_efi()
    return core.plan_replacement(snapshot, {"action": "repair_boot", "pool_guid": "1000", "target": snapshot["disks"][0]["by_id"]})


def test_online_zfs_does_not_hide_missing_fat32_on_original_disk():
    snapshot = defective_efi()
    assert snapshot["pools"][0]["state"] == "ONLINE"
    report = boot.boot_report(snapshot, snapshot["pools"][0])
    assert report["configuration_ready"] is False
    assert report["disks"][0]["checks"]["fat32"]["state"] == "ERROR"
    assert report["disks"][1]["configuration_ready"] is True
    assert report["autonomous_boot"] == "NON_TESTATO"


def test_healthy_fallback_does_not_require_a_second_nvram_entry():
    snapshot = inventory()
    report = boot.boot_report(snapshot, snapshot["pools"][0])
    assert report["configuration_ready"]
    assert not any(r["nvram_entry_ids"] for r in report["disks"])
    assert report["autonomous_boot"] == "NON_TESTATO"


@pytest.mark.parametrize("change,check", [
    (lambda s, p, i: i.update(fsck_code=1), "filesystem"),
    (lambda s, p, i: i.update(inspection_ok=False, files=[]), "loader"),
    (lambda s, p, i: s["boot"].update(registered_uuids=[]), "registration"),
    (lambda s, p, i: p.update(uuid="STALE-UUID"), "identity"),
    (lambda s, p, i: i.update(files=[]), "loader"),
    (lambda s, p, i: i.update(files=[f for f in i["files"] if not f["path"].startswith("EFI/BOOT/")]), "uefi_path"),
    (lambda s, p, i: i.update(files=[f for f in i["files"] if "initrd" not in f["path"]]), "configuration"),
])
def test_each_missing_boot_evidence_prevents_ready(change, check):
    snapshot = inventory()
    part = snapshot["disks"][0]["partitions"][1]
    info = snapshot["boot"]["esps"][part["path"]]
    change(snapshot, part, info)
    row = boot.boot_report(snapshot, snapshot["pools"][0])["disks"][0]
    assert not row["configuration_ready"]
    assert row["checks"][check]["state"] != "OK"


def test_uefi_entry_requires_matching_partuuid_existing_pe_and_boot_order():
    snapshot = inventory()
    part = snapshot["disks"][0]["partitions"][1]
    info = snapshot["boot"]["esps"][part["path"]]
    info["files"] = [f for f in info["files"] if "BOOTX64" not in f["path"]]
    text = "BootCurrent: 0003\nBootOrder: 0003\nBoot0003* Linux Boot Manager HD(2,GPT," + part["partuuid"] + ",0x800,0x100000)/File(\\EFI\\systemd\\systemd-bootx64.efi)"
    snapshot["boot"]["nvram"] = {"code": 0, **boot.parse_boot_entries(text)}
    row = boot.boot_report(snapshot, snapshot["pools"][0])["disks"][0]
    assert row["checks"]["uefi_path"]["state"] == "OK"
    snapshot["boot"]["nvram"]["entries"][0]["partuuid"] = "wrong-disk"
    assert boot.boot_report(snapshot, snapshot["pools"][0])["disks"][0]["checks"]["uefi_path"]["state"] == "ERROR"


def pe_image(marker=b"GRUB"):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, 0x8664)
    struct.pack_into("<H", data, 152, 0x20B)
    struct.pack_into("<H", data, 220, 10)
    data[512:512 + len(marker)] = marker
    return bytes(data)


def test_efi_binary_requires_real_pe_header_and_efi_subsystem():
    assert boot.efi_executable(pe_image())
    assert not boot.efi_executable(b"MZ" + bytes(1024))
    corrupt = bytearray(pe_image())
    struct.pack_into("<H", corrupt, 220, 3)
    assert not boot.efi_executable(corrupt)
    struct.pack_into("<I", corrupt, 60, 2**31)
    assert not boot.efi_executable(corrupt)


def test_read_only_file_inventory_identifies_standalone_grub_fallback(tmp_path):
    path = tmp_path / "EFI/BOOT/BOOTX64.EFI"
    path.parent.mkdir(parents=True)
    path.write_bytes(pe_image())
    files = boot.file_inventory(tmp_path)
    assert files[0]["efi_valid"] is True
    assert files[0]["loader_kind"] == "grub"
    assert path.read_bytes() == pe_image()


def test_esp_probe_mount_is_read_only_private_and_always_unmounted(monkeypatch, tmp_path):
    actual_stat = boot.os.stat
    def fake_stat(path, *args, **kwargs):
        return SimpleNamespace(st_mode=stat.S_IFBLK) if str(path) == "/dev/testESP" else actual_stat(path, *args, **kwargs)
    monkeypatch.setattr(boot.os, "stat", fake_stat)
    monkeypatch.setattr(boot.tempfile, "TemporaryDirectory", lambda **kw: contextlib.nullcontext(str(tmp_path)))
    monkeypatch.setattr(boot, "file_inventory", lambda p: [])
    commands = []
    def run(args):
        commands.append(args)
        return {"code": 0, "out": json.dumps({"blockdevices": [{"type": "part", "parttype": core.ESP}]}), "err": ""}
    boot.inspect_esp_namespace("/dev/testESP", run)
    assert commands[1] == ["mount", "-t", "vfat", "-o", "ro,nodev,nosuid,noexec", "/dev/testESP", str(tmp_path)]
    assert commands[-1] == ["umount", str(tmp_path)]


def test_missing_boot_observations_never_certify_boot():
    snapshot = inventory()
    snapshot["boot"] = {"status": "", "code": 0}
    assert not boot.boot_report(snapshot, snapshot["pools"][0])["configuration_ready"]


def test_esp_only_repair_never_repartitions_or_replaces_zfs():
    plan = repair_plan()
    assert plan["blocks"] == []
    assert plan["action"] == "repair_boot"
    assert all(s["argv"][0] == "proxmox-boot-tool" for s in plan["steps"])
    assert plan["steps"][0]["argv"] == ["proxmox-boot-tool", "format", "/dev/disk/by-id/ata-DEMO_BOOT_A-part2"]
    assert ["proxmox-boot-tool", "init", "/dev/disk/by-id/ata-DEMO_BOOT_A-part2", "grub"] in [s["argv"] for s in plan["steps"]]
    assert ["proxmox-boot-tool", "refresh"] in [s["argv"] for s in plan["steps"]]
    assert not any("-part3" in s["command"] for s in plan["steps"])


@pytest.mark.parametrize("change", [
    lambda s: s["boot"].update(firmware_mode="BIOS"),
    lambda s: s["boot"].update(architecture="aarch64"),
    lambda s: s["pools"][0].update(state="DEGRADED"),
    lambda s: s["disks"][0]["partitions"][1].update(number=3),
    lambda s: s["disks"][0]["partitions"][1].update(mounts=["/boot/efi"]),
    lambda s: s["boot"]["esps"]["/dev/sda2"].update(probe_code=0, probe={"TYPE": "ext4"}, signatures=[{"type": "ext4"}]),
    lambda s: s["boot"]["esps"]["/dev/sdb2"].update(fsck_code=1),
])
def test_repair_blocks_ambiguous_and_unsafe_boot_conditions(change):
    snapshot = defective_efi()
    change(snapshot)
    assert repair_plan(snapshot)["blocks"]


def test_valid_existing_fat32_is_never_formatted_when_reinstalling_loader():
    snapshot = inventory()
    demo.add_boot_observations(snapshot, "grub")
    snapshot["boot"]["esps"]["/dev/sda2"]["files"] = []
    plan = repair_plan(snapshot)
    assert not plan["blocks"]
    assert all(s["argv"][:2] != ["proxmox-boot-tool", "format"] for s in plan["steps"])


@pytest.mark.parametrize("change", [
    lambda row: row.update(partuuid="another-partition"),
    lambda row: row.update(parttype=core.ZFS),
    lambda row: row.update(mountpoints=["/boot/efi"]),
])
def test_live_esp_binding_blocks_changed_identity_wrong_type_or_mount(monkeypatch, change):
    plan = repair_plan()
    row = {"type": "part", "parttype": core.ESP,
           "partuuid": plan["expected_esp"]["partuuid"], "mountpoints": [None]}
    change(row)
    monkeypatch.setattr(node, "verify_partition", lambda *args: None)
    monkeypatch.setattr(node, "run", lambda *args: {"code": 0, "out": json.dumps({"blockdevices": [row]}), "err": ""})
    with pytest.raises(core.SafetyError):
        node.bind_esp(plan)


def test_live_format_guard_blocks_filesystem_created_after_approval(monkeypatch):
    plan = repair_plan()
    step = next(s for s in plan["steps"] if s["argv"][:2] == ["proxmox-boot-tool", "format"])
    monkeypatch.setattr(node, "bind_esp", lambda p: p.update(expected_esp={"path": step["argv"][2]}))
    calls = []

    def probe(argv):
        calls.append(argv)
        return {"code": 0, "out": "TYPE=vfat\n" if argv[0] == "blkid" else json.dumps({"signatures": [{"type": "vfat"}]}), "err": ""}

    monkeypatch.setattr(node, "run", probe)
    with pytest.raises(core.SafetyError, match="formattazione annullata"):
        node.verify_boot_write(plan, step)
    assert [c[0] for c in calls] == ["blkid", "wipefs"]


@pytest.fixture
def journal(monkeypatch, tmp_path):
    monkeypatch.setattr(node, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(node, "locked", lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(node, "verify_target", lambda p: None)
    monkeypatch.setattr(node, "verify_partition", lambda *a: None)
    monkeypatch.setattr(node, "bind_esp", lambda *a: None)
    monkeypatch.setattr(node, "verify_copied_layout", lambda *a: None)
    return tmp_path


def root_job(monkeypatch, state="MONITORING"):
    snapshot = boot_inventory(grub=True)
    monkeypatch.setattr(node, "collect", lambda: snapshot)
    plan = node.prepare({"pool_guid": "1000", "old_guid": "102", "target": "/dev/disk/by-id/ata-DEMO_NEW", "profile": "laboratorio"})
    plan["state"] = state
    node.save_record(plan)
    return plan


@pytest.mark.parametrize("damage", ["source", "target", None])
def test_boot_never_becomes_completed_from_online_mirror_alone(journal, monkeypatch, damage):
    plan = root_job(monkeypatch)
    snapshot = rebuilt()
    if damage:
        target = next(d for d in snapshot["disks"] if d["serial"] == ("DEMO_BOOT_A" if damage == "source" else "DEMO_NEW"))
        snapshot["boot"]["esps"][target["partitions"][1]["path"]]["files"] = []
    monkeypatch.setattr(node, "collect", lambda: snapshot)
    result = node.get_status(plan["id"])
    assert result["zfs_state"] == "OK"
    assert result["state"] == ("BOOT_REVIEW_REQUIRED" if damage else "BOOT_TEST_REQUIRED")
    assert result["boot_test"] == "NON_TESTATO_COMPLETAMENTE"


def test_legacy_completed_boot_job_is_reassessed(journal, monkeypatch):
    plan = root_job(monkeypatch, "COMPLETED")
    plan.pop("helper_version")
    node.save_record(plan)
    monkeypatch.setattr(node, "collect", rebuilt)
    assert node.get_status(plan["id"])["state"] == "BOOT_TEST_REQUIRED"


def test_boot_commands_wait_for_resilver_and_cursor_prevents_replaying_replace(journal, monkeypatch):
    plan = root_job(monkeypatch, "QUEUED")
    calls = []
    monkeypatch.setattr(node, "run", lambda args, **k: calls.append(args) or {"code": 0, "out": "", "err": ""})
    node.perform_job(plan["id"])
    waiting = node.load_record(plan["id"])
    assert waiting["state"] == "WAITING_RESILVER"
    assert calls[-1][0:2] == ["zpool", "replace"]
    assert all(c[0] != "proxmox-boot-tool" for c in calls)
    assert waiting["steps"][waiting["next_step"]]["argv"][:2] == ["proxmox-boot-tool", "format"]
    monkeypatch.setattr(node, "collect", rebuilt)
    monkeypatch.setattr(node, "spawn_worker", lambda *a: 123)
    queued = node.get_status(plan["id"])
    assert queued["state"] == "QUEUED" and queued["execution_phase"] == "boot"
    monkeypatch.setattr(node, "verify_boot_write", lambda *a: None)
    node.perform_job(plan["id"])
    assert sum(c[:2] == ["zpool", "replace"] for c in calls) == 1
    assert ["proxmox-boot-tool", "refresh"] in calls


def test_post_resilver_failure_of_source_boot_prevents_esp_writes(journal, monkeypatch):
    plan = root_job(monkeypatch, "WAITING_RESILVER")
    snapshot = rebuilt()
    snapshot["boot"]["esps"]["/dev/sda2"]["files"] = []
    monkeypatch.setattr(node, "collect", lambda: snapshot)
    monkeypatch.setattr(node, "spawn_worker", lambda *a: pytest.fail("Must not start a format"))
    assert node.get_status(plan["id"])["state"] == "BOOT_REVIEW_REQUIRED"


def single_disk_boot(plan, serial, boot_id):
    snapshot = rebuilt()
    snapshot["boot_id"] = boot_id
    snapshot["boot_started_at"] = plan["created_at"] + 10
    snapshot["pools"][0]["state"] = snapshot["pools"][0]["children"][0]["state"] = "DEGRADED"
    for member in core.leaves(snapshot["pools"][0]):
        disk = core.disk_for(snapshot, member["name"])
        if disk and disk["serial"] != serial:
            member["state"] = "UNAVAIL"
            snapshot["disks"].remove(disk)
    demo.add_boot_observations(snapshot, "grub")
    disk = next(d for d in snapshot["disks"] if d["serial"] == serial)
    esp = next(p for p in disk["partitions"] if p["parttype"] == core.ESP)
    snapshot["boot"]["nvram"] = {"code": 0, "current": "000A", "order": ["000A"],
        "entries": [{"id": "000A", "active": True, "partuuid": esp["partuuid"], "file": "efi/boot/bootx64.efi"}]}
    return snapshot


def test_autonomous_boot_proof_requires_new_boot_and_one_real_member(journal, monkeypatch):
    plan = root_job(monkeypatch, "BOOT_TEST_REQUIRED")
    monkeypatch.setattr(node, "collect", rebuilt)
    req = {"id": plan["id"], "serial": "DEMO_NEW", "confirmed": True}
    with pytest.raises(core.SafetyError, match="solo disco"):
        node.record_boot_test(req)
    single = single_disk_boot(plan, "DEMO_NEW", "NEW-BOOT")
    single["boot_started_at"] = plan["created_at"] - 10
    monkeypatch.setattr(node, "collect", lambda: single)
    with pytest.raises(core.SafetyError, match="nuovo avvio"):
        node.record_boot_test(req)


def test_completion_requires_two_distinct_physical_boots_and_restored_mirror(journal, monkeypatch):
    plan = root_job(monkeypatch, "BOOT_TEST_REQUIRED")
    for serial, boot_id in (("DEMO_NEW", "NEW-BOOT-ONE"), ("DEMO_BOOT_A", "NEW-BOOT-TWO")):
        snapshot = single_disk_boot(plan, serial, boot_id)
        monkeypatch.setattr(node, "collect", lambda s=snapshot: s)
        result = node.record_boot_test({"id": plan["id"], "serial": serial, "confirmed": True})
        assert result["state"] != "COMPLETED"
    monkeypatch.setattr(node, "collect", rebuilt)
    result = node.get_status(plan["id"])
    assert result["state"] == "COMPLETED"
    assert result["boot_test"] == "VERIFICATO_DALL_OPERATORE"


def test_missing_efi_source_blocks_new_boot_disk_replacement():
    snapshot = boot_inventory()
    snapshot["boot"]["esps"]["/dev/sda2"]["files"] = []
    result = core.plan_replacement(snapshot, {"pool_guid": "1000", "old_guid": "102", "target": "/dev/disk/by-id/ata-DEMO_NEW"})
    assert any("riferimento" in reason for reason in result["blocks"])


def test_boot_current_from_another_device_cannot_certify_autonomous_boot(journal, monkeypatch):
    plan = root_job(monkeypatch, "BOOT_TEST_REQUIRED")
    snapshot = single_disk_boot(plan, "DEMO_NEW", "NEW-BOOT")
    snapshot["boot"]["nvram"]["entries"][0]["partuuid"] = "unrelated-usb-esp"
    monkeypatch.setattr(node, "collect", lambda: snapshot)
    with pytest.raises(core.SafetyError, match="BootCurrent"):
        node.record_boot_test({"id": plan["id"], "serial": "DEMO_NEW", "confirmed": True})


def test_boot_proof_no_longer_applies_after_efi_contents_change(journal, monkeypatch):
    plan = root_job(monkeypatch, "BOOT_TEST_REQUIRED")
    for serial, boot_id in (("DEMO_NEW", "FIRST-BOOT"), ("DEMO_BOOT_A", "SECOND-BOOT")):
        snapshot = single_disk_boot(plan, serial, boot_id)
        monkeypatch.setattr(node, "collect", lambda s=snapshot: s)
        node.record_boot_test({"id": plan["id"], "serial": serial, "confirmed": True})
    changed = rebuilt()
    changed["boot"]["esps"]["/dev/sda2"]["files"][0]["sha256"] = "changed-efi-binary"
    monkeypatch.setattr(node, "collect", lambda: changed)
    assert node.get_status(plan["id"])["state"] == "BOOT_TEST_REQUIRED"
