from copy import deepcopy

import pytest

from pve_disk_assistant import core, demo


def inventory(raidz=False):
    snap = demo.snapshot(raidz)
    snap.pop("demo")
    return snap


def request(**changes):
    result = {"pool_guid": "2000", "old_guid": "202", "target": "/dev/disk/by-id/ata-DEMO_NEW", "profile": "laboratorio"}
    result.update(changes)
    return result


def plan(snap=None, **changes):
    return core.plan_replacement(snap or inventory(), request(**changes))


def test_mirror_and_raidz_keep_missing_members():
    for raidz, count, kind in [(False, 2, "mirror"), (True, 3, "raidz1")]:
        snap = inventory(raidz)
        group = core.data_groups(snap["pools"][1])[0]
        assert group["kind"] == kind
        assert len(core.leaves(group)) == count
        absent = next(m for m in core.leaves(group) if m["state"] == "UNAVAIL")
        assert absent["guid"] == "202"
        assert absent["was_path"] == "/dev/disk/by-id/ata-DEMO_VM_B"


def test_boot_mixed_and_vm_roles_come_from_mounts_and_config():
    snap = inventory()
    assert [p["role"] for p in snap["pools"]] == ["MISTO", "DATI_VM"]
    snap["pools"][0]["name"] = "arbitrary-root"
    snap["root"]["source"] = "arbitrary-root/ROOT/test"
    snap["storages"][0]["pool"] = "arbitrary-root/data"
    core.classify(snap)
    assert snap["pools"][0]["role"] == "MISTO"


def test_missing_root_information_never_classifies_as_data():
    snap = inventory()
    snap["root"] = {}
    core.classify(snap)
    assert all(p["role"] == "SCONOSCIUTO" for p in snap["pools"])


def test_resilver_percentage_and_eta_are_kept_from_continuation_lines():
    status = demo.STATUS.replace("scan: none requested", "scan: resilver in progress since Sun Nov 1\n    22G scanned, 12G issued\n    12G resilvered, 24.50% done, 00:12:34 to go")
    pool = core.parse_status(status)[1]
    assert pool["scan_percent"] == 24.5
    assert "00:12:34 to go" in pool["scan"]


@pytest.mark.parametrize("raidz", [False, True])
def test_supported_vm_replacement_has_no_boot_or_force_commands(raidz):
    result = plan(inventory(raidz))
    assert result["blocks"] == []
    assert [s["argv"][0] for s in result["steps"]] == ["zpool"]
    assert result["steps"][0]["argv"] == ["zpool", "replace", "vmdata", "202", "/dev/disk/by-id/ata-DEMO_NEW"]
    assert "-f" not in result["steps"][0]["argv"]


@pytest.mark.parametrize("mutation, expected", [
    (lambda s: s.update(complete=False), "incompleto"),
    (lambda s: s.update(pve_version="pve-manager/7.4/old"), "Versione"),
    (lambda s: s["disks"][3].update(serial=""), "Seriale"),
    (lambda s: s["disks"][3].update(serial="DEMO_VM_A"), "Seriale"),
    (lambda s: s["disks"][3].update(size=10), "Capacità"),
    (lambda s: s["disks"][3].update(signatures=[{"type": "ext4"}]), "non è vuoto"),
    (lambda s: s["disks"][3].update(holders=["/dev/dm-0"]), "non è vuoto"),
    (lambda s: s["disks"][3].update(mounts=["[SWAP]"]), "non è vuoto"),
    (lambda s: s["disks"][3].update(ro=True), "non è vuoto"),
    (lambda s: s["disks"][3].update(signatures_checked=False), "firme"),
    (lambda s: s["disks"][3].update(identity_verified=False), "persistente"),
    (lambda s: s["disks"][3].update(log_sec=4096), "settori"),
    (lambda s: s["pools"][1].update(role="ALTRO"), "Ruolo"),
    (lambda s: s["pools"][1].update(errors="1 data errors"), "permanenti"),
    (lambda s: s["pools"][1].update(state="FAULTED"), "non operativo"),
    (lambda s: s["pools"][1].update(scan="resilver in progress"), "in corso"),
    (lambda s: s["pools"][1].update(warnings=["Parsing ambiguo"]), "ambiguo"),
    (lambda s: core.leaves(s["pools"][1])[0].update(errors=[0, 0, 1]), "rimanente"),
    (lambda s: core.leaves(s["pools"][1])[0].update(state="OFFLINE"), "rimanente"),
    (lambda s: s["disks"][2]["smart"].update(passed=False), "SMART"),
    (lambda s: s["disks"][2]["smart"].update(exit_code=8), "SMART"),
    (lambda s: s.update(tools=[]), "Strumenti"),
])
def test_blocks_unsafe_inventory(mutation, expected):
    snap = inventory()
    mutation(snap)
    assert any(expected in b for b in plan(snap)["blocks"])


def test_cannot_select_healthy_or_already_used_target():
    assert any("non risulta guasto" in b for b in plan(old_guid="201")["blocks"])
    assert any("già a un pool" in b for b in plan(target="/dev/disk/by-id/ata-DEMO_VM_A")["blocks"])


def test_demo_can_never_be_executed():
    assert any("dimostrativa" in b for b in plan(demo.snapshot())["blocks"])


def test_missing_guids_reject_instead_of_guessing():
    with pytest.raises(core.SafetyError):
        plan(old_guid="nonexistent")


def test_hot_swap_is_an_explicit_capability():
    assert any("hot swap" in b for b in plan(profile="produzione")["blocks"])
    assert not plan(profile="produzione", hot_swap_confirmed=True)["blocks"]


def test_unknown_states_do_not_disappear_silently():
    pools = core.parse_status(demo.STATUS.replace("202                                    UNAVAIL", "202                                    MYSTERY"))
    assert pools[1]["warnings"]


def test_guid_join_refuses_changing_topology():
    pools = core.parse_status(demo.STATUS)
    core.merge_guids(pools, demo.STATUS.replace("UNAVAIL", "ONLINE"), {"rpool": "1", "vmdata": "2"})
    assert pools[1]["warnings"]
    assert not core.leaves(pools[1])[0]["guid"]


def test_auxiliary_vdevs_block_execution():
    snap = inventory()
    special = deepcopy(core.data_groups(snap["pools"][1])[0])
    special["section"] = "special"
    snap["pools"][1]["children"].append(special)
    assert any("ausiliarie" in b for b in plan(snap)["blocks"])


def test_more_than_one_data_vdev_is_not_treated_as_one_mirror():
    snap = inventory()
    snap["pools"][1]["children"].append(deepcopy(snap["pools"][1]["children"][0]))
    assert any("un vdev" in b for b in plan(snap)["blocks"])


def test_three_disks_are_not_assumed_to_be_raidz1():
    snap = inventory(True)
    group = core.data_groups(snap["pools"][1])[0]
    group["kind"] = "raidz2"
    assert "RAIDZ2" in plan(snap)["raid"]
    group["kind"] = "disk"
    assert "SCONOSCIUTO" in core.raid_label(snap["pools"][1]) or "UNKNOWN" in core.raid_label(snap["pools"][1])
    assert plan(snap)["blocks"]


def boot_inventory(grub=False):
    snap = inventory()
    pool = snap["pools"][0]
    member = core.leaves(pool)[1]
    member["state"] = "UNAVAIL"
    pool["state"] = "DEGRADED"
    pool["children"][0]["state"] = "DEGRADED"
    snap["disks"] = [d for d in snap["disks"] if d["serial"] != "DEMO_BOOT_B"]
    snap["boot"]["status"] = "ABCD-1234 is configured with: " + ("grub" if grub else "uefi") + " (versions: 6.x)"
    return snap


@pytest.mark.parametrize("grub", [False, True])
def test_boot_plan_copies_in_the_correct_direction_and_chooses_bootloader(grub):
    result = plan(boot_inventory(grub), pool_guid="1000", old_guid="102")
    assert result["blocks"] == []
    args = [s["argv"] for s in result["steps"]]
    assert args[0] == ["sgdisk", "/dev/disk/by-id/ata-DEMO_BOOT_A", "-R", "/dev/disk/by-id/ata-DEMO_NEW"]
    assert ["zpool", "replace", "rpool", "102", "/dev/disk/by-id/ata-DEMO_NEW-part3"] in args
    assert ["proxmox-boot-tool", "format", "/dev/disk/by-id/ata-DEMO_NEW-part2"] in args
    init = next(a for a in args if a[:2] == ["proxmox-boot-tool", "init"])
    assert (init[-1] == "grub") is grub


def test_unknown_bootloader_blocks_boot_modification():
    snap = boot_inventory()
    snap["boot"]["status"] = "proxmox-boot-tool is not configured"
    assert any("Modalità" in b for b in plan(snap, pool_guid="1000", old_guid="102")["blocks"])


def test_boot_esp_can_never_be_the_zfs_partition():
    snap = boot_inventory()
    source = snap["disks"][0]
    source["partitions"][1]["parttype"] = core.ZFS
    source["partitions"][2].update(parttype=core.ESP, fstype="vfat", uuid="ABCD-1234")
    result = plan(snap, pool_guid="1000", old_guid="102")
    assert result["blocks"]
    assert all(s["argv"][:2] != ["proxmox-boot-tool", "format"] for s in result["steps"])


def test_duplicate_boot_partition_numbers_block_format():
    snap = boot_inventory()
    snap["disks"][0]["partitions"][2]["number"] = 2
    result = plan(snap, pool_guid="1000", old_guid="102")
    assert result["blocks"]
    assert all(s["argv"][:2] != ["proxmox-boot-tool", "format"] for s in result["steps"])


def test_no_live_sata_disconnect_on_lab():
    snap = boot_inventory()
    removed = demo.snapshot()["disks"][1]
    snap["disks"].append(removed)
    assert any("senza hot swap" in b for b in plan(snap, pool_guid="1000", old_guid="102")["blocks"])


def test_recognized_data_gpt_is_replicated_without_boot_commands():
    snap = inventory()
    disk = snap["disks"][2]
    alias = disk["by_id"] + "-part1"
    disk["partitions"] = [{"path": "/dev/sdc1", "aliases": [alias], "number": 1, "parttype": core.ZFS}]
    disk["table"] = {"label": "gpt"}
    core.leaves(snap["pools"][1])[0]["name"] = alias
    result = plan(snap)
    assert not result["blocks"]
    assert result["new_member"].endswith("-part1")
    assert result["steps"][0]["argv"][0] == "sgdisk"
    assert all(s["argv"][0] != "proxmox-boot-tool" for s in result["steps"])


def test_report_redacts_raw_and_nested_identifiers_consistently():
    snap = inventory()
    snap["disks"][0]["signatures"] = [{"label": "ACME confidential", "uuid": "secret-uuid"}]
    public = core.anonymize(snap)
    text = str(public)
    for secret in ["DEMO_BOOT_A", "DEMO-HOST", "pve-laboratorio", "ACME confidential", "secret-uuid", "Gestionale", "rpool/data"]:
        assert secret not in text
    assert public["pools"][1]["state"] == "DEGRADED"
    assert public["disks"][0]["serial"] == public["disks"][0]["serial"]


def offline_inventory():
    snap = inventory()
    missing = deepcopy(snap["disks"][2])
    missing.update(path="/dev/sdz", by_id="/dev/disk/by-id/ata-DEMO_VM_B", serial="DEMO_VM_B",
                   aliases=["/dev/disk/by-id/ata-DEMO_VM_B"])
    snap["disks"].append(missing)
    member = core.leaves(snap["pools"][1])[1]
    member.update(name=missing["by_id"], state="FAULTED")
    return snap


def test_hot_removal_is_a_separate_approved_offline_plan():
    result = plan(offline_inventory(), action="offline", profile="produzione", hot_swap_confirmed=True)
    assert result["blocks"] == []
    assert result["action"] == "offline"
    assert result["steps"][0]["argv"] == ["zpool", "offline", "vmdata", "202"]
    assert result["target"]["serial"] == "DEMO_VM_B"


@pytest.mark.parametrize("mutate", [
    lambda s: core.leaves(s["pools"][1])[0].update(state="DEGRADED"),
    lambda s: core.leaves(s["pools"][1])[0].update(errors=[0, 1, 0]),
    lambda s: core.leaves(s["pools"][1])[1].update(state="ONLINE"),
    lambda s: s.update(complete=False),
    lambda s: s["pools"][1].update(role="SCONOSCIUTO"),
])
def test_hot_removal_cannot_bypass_peer_or_identity_checks(mutate):
    snap = offline_inventory()
    mutate(snap)
    assert plan(snap, action="offline", profile="produzione", hot_swap_confirmed=True)["blocks"]


def test_offline_not_offered_for_absent_or_lab_member():
    with pytest.raises(core.SafetyError, match="assente"):
        plan(action="offline", profile="produzione", hot_swap_confirmed=True)
    assert plan(offline_inventory(), action="offline")["blocks"]


def test_reused_sd_path_does_not_claim_a_blank_replacement_as_old_member():
    snap = inventory()
    old = core.leaves(snap["pools"][1])[1]
    old["name"] = snap["disks"][3]["path"]
    assert core.disk_for_member(snap, old) is None
    assert not plan(snap)["blocks"]

