"""Synthetic recovery scenarios for the 0.6 lab candidate."""
import contextlib
from copy import deepcopy

import pytest

from pve_disk_assistant import core, identity, node, workflow
from test_core import inventory, request
from test_node import sandbox


@pytest.mark.parametrize("version", ["9.3", "9.2", "9.10", "10.0", "8.40"])
def test_unknown_profiles_block_writes(version):
    snap = inventory()
    snap["pve_version"] = f"pve-manager/{version}.0/synthetic"
    assert core.VERSION_BLOCK in core.plan_replacement(snap, request())["blocks"]


def test_bare_alias_resolves_only_one_physical_disk():
    snap = inventory()
    device = snap["disks"][0]["partitions"][-1]
    alias = device["aliases"][0].split("/")[-1]
    assert core.disk_for(snap, alias) is snap["disks"][0]
    assert core.partition_for(snap["disks"][0], alias) is device
    snap["disks"][1]["partitions"][-1]["aliases"].append(device["aliases"][0])
    assert core.disk_for(snap, alias) is None


def test_partial_serial_and_dynamic_path_cannot_establish_identity():
    disk = inventory()["disks"][0]
    disk.update(serial="SERIAL1", aliases=["/dev/disk/by-id/ata-brand_SERIAL123"])
    assert not identity.serial_alias(disk)
    disk["by_id"] = disk["path"]
    assert not identity.physical_identity_verified([disk], disk)


@pytest.mark.parametrize("second_guid", ["101", "ata-unresolved"])
def test_duplicate_or_missing_leaf_guids_block_pool(second_guid):
    from pve_disk_assistant.demo import STATUS
    named = STATUS.split("  pool: vmdata")[0]
    guids = named.replace("/dev/disk/by-id/ata-DEMO_BOOT_A-part3", "101").replace(
        "/dev/disk/by-id/ata-DEMO_BOOT_B-part3", second_guid)
    pools = core.parse_status(named)
    core.merge_guids(pools, guids, {"rpool": "1000"})
    assert pools[0]["warnings"]


@pytest.mark.parametrize("duplicate", [False, True])
def test_online_numeric_guid_requires_unique_on_disk_pool_and_vdev_label(monkeypatch, duplicate):
    snap = inventory()
    pool = snap["pools"][0]
    member = core.leaves(pool)[0]
    member["name"] = member["guid"]
    observed = snap["disks"][0]["partitions"][-1]
    def probe(args):
        matching = args[-1] == observed["path"] or duplicate
        return {"code": 0, "out": "TYPE=zfs_member\nUUID=1000\nUUID_SUB=" + (member["guid"] if matching else "0")}
    monkeypatch.setattr(node, "run", probe)
    node.resolve_numeric_members(snap)
    if duplicate:
        assert pool["warnings"] and not snap["complete"]
    else:
        assert member["reported_name"] == member["guid"]
        assert core.partition_for(snap["disks"][0], member["name"]) == observed


def test_lock_contention_never_changes_active_job(sandbox, monkeypatch):
    plan = node.prepare(request())
    plan["state"] = "RUNNING"
    node.save_record(plan)
    @contextlib.contextmanager
    def busy(*args):
        raise node.OperationBusy("busy")
        yield
    monkeypatch.setattr(node, "locked", busy)
    monkeypatch.setattr(node.time, "sleep", lambda _: None)
    with pytest.raises(node.OperationBusy):
        node.perform_job(plan["id"])
    assert node.get_status(plan["id"]) == node.load_record(plan["id"]) == plan


def test_queue_age_begins_at_approval_and_orphan_is_never_replayed(sandbox):
    plan = node.prepare(request())
    plan.update(state="QUEUED", created_at=1, queued_at=node.time.time())
    node.save_record(plan)
    assert node.get_status(plan["id"])["state"] == "QUEUED"
    plan.update(state="RUNNING")
    node.save_record(plan)
    result = node.get_status(plan["id"])
    assert result["state"] == "UNKNOWN"
    assert result["phases"]["Sostituzione ZFS"] == "Interrotta"


def test_old_pending_job_survives_fifty_new_previews(sandbox):
    original = node.prepare(request())
    original.update(state="WAITING_RESILVER", created_at=0)
    node.save_record(original)
    for n in range(1, 53):
        node.save_record({"id": f"{n:032x}", "state": "PREVIEW", "created_at": n})
    jobs = node.dispatch({"op": "list_jobs"})
    assert jobs[0] == original and len(jobs) == 51


@pytest.mark.parametrize("change", ["unchanged_scan", "wrong_slot", "old_guid", "other_host", "disk_ambiguous"])
def test_monitor_rejects_online_without_matching_substitution(sandbox, change):
    plan = node.prepare(request())
    snap = deepcopy(plan["before"])
    pool = snap["pools"][1]
    group = core.data_groups(pool)[0]
    pool["state"] = group["state"] = "ONLINE"
    member = group["children"][1]
    member.update(state="ONLINE", guid="999", name=plan["new_member"])
    if change != "unchanged_scan":
        pool["scan"] = "resilvered 45G in 00:38:00 with 0 errors"
    if change == "old_guid":
        member["guid"] = plan["old_guid"]
    if change == "wrong_slot":
        group["children"].reverse()
    if change == "other_host":
        snap["host_id"] += "-other"
    if change == "disk_ambiguous":
        core.disk_for(snap, member["name"])["identity_verified"] = False
    assert not node.zfs_ready(plan, snap, pool)


def test_successful_command_alone_does_not_verify_resilver():
    phases = workflow.replacement_phases({"action": "replace", "role": "DATI_VM", "state": "MONITORING",
                                           "events": [{"phase": "replace", "state": "OK"}]})
    assert phases["Resilver"] == "In attesa di verifica"
    assert phases["Avvio fisico"] == "Non applicabile"


def test_production_profile_cannot_approve_replacement():
    assert core.PRODUCTION_BLOCK in core.plan_replacement(inventory(), {
        **request(), "profile": "produzione", "hot_swap_confirmed": True})["blocks"]

def test_boot_monitor_requires_the_planned_zfs_partition(monkeypatch, sandbox):
    from test_boot import rebuilt, root_job
    plan = root_job(monkeypatch)
    snap = rebuilt()
    pool = snap["pools"][0]
    assert node.zfs_ready(plan, snap, pool)
    member = core.leaves(pool)[1]
    member["name"] = plan["target"]["by_id"] + "-part2"
    assert not node.zfs_ready(plan, snap, pool)
