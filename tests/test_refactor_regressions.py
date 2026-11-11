"""Synthetic regressions for the reported ESP checks and command/status split."""
import io
import json
import time
from copy import deepcopy

import pytest

from pve_disk_assistant import core, esp, identity, node, presentation, workflow
from test_esp_initialization import replacement, request


def test_real_blkid_export_header_is_metadata_not_a_filesystem(monkeypatch):
    path = "/dev/disk/by-id/wwn-synthetic-part2"
    output = "DEVNAME=" + path + "\nPART_ENTRY_SCHEME=gpt\nPART_ENTRY_UUID=synthetic-partition\nPART_ENTRY_TYPE=" + core.ESP + "\nPART_ENTRY_NUMBER=2\nPART_ENTRY_SIZE=2048\nPART_ENTRY_DISK=8:0\n"
    def run(argv):
        return {"code": 0, "out": output if argv[0] == "blkid" else '{"signatures": []}', "err": ""}
    monkeypatch.setattr(esp, "read_block_device", lambda p, size: esp.read_all_zero(io.BytesIO(bytes(size)), size, time.monotonic() + 5))
    result = esp.blank_esp(path, 4096, run)
    assert result["state"] == "EMPTY" and result["bytes_read"] == 4096
    assert len(result["checks"]) == 2 and result["checks"][0]["code"] == 0
    for invalid in (output.replace(path, "/dev/wrong"), output + "TYPE=vfat\n", output + "PART_ENTRY_NUMBER=3\n"):
        with pytest.raises(esp.EspCheckError):
            esp.verify_empty_signatures({"code": 0, "out": invalid}, {"code": 0, "out": '{"signatures": []}'}, path)


def test_probe_failure_records_exact_command_result_without_claiming_zero_bytes(monkeypatch):
    monkeypatch.setattr(esp, "read_block_device", lambda *a: pytest.fail("Do not read after ambiguous signatures"))
    with pytest.raises(esp.EspCheckError) as caught:
        esp.blank_esp("/dev/synthetic2", 4096, lambda argv: {"code": 8 if argv[0] == "blkid" else 0,
                      "out": "" if argv[0] == "blkid" else '{"signatures": []}', "err": "ambivalent" if argv[0] == "blkid" else ""})
    evidence = caught.value.evidence
    assert evidence["state"] == "PROBE_FAILED"
    assert evidence["checks"][0]["code"] == 8
    assert "lettura non avviata" in presentation.scan_summary({"blank": evidence})
    assert "Byte letti" not in presentation.scan_summary({"blank": evidence})


def test_short_read_and_io_error_preserve_bytes_actually_read():
    class Broken:
        def __init__(self):
            self.calls = 0
        def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return bytes(17)
            raise OSError("synthetic I/O failure")
    for stream, state in ((io.BytesIO(bytes(17)), "INCOMPLETE"), (Broken(), "READ_ERROR")):
        with pytest.raises(esp.EspCheckError) as caught:
            esp.read_all_zero(stream, 100, time.monotonic() + 5)
        assert caught.value.evidence["state"] == state
        assert caught.value.evidence["bytes_read"] == 17


def test_same_wwn_formats_are_normalized_across_journal_and_live_checks(replacement, monkeypatch):
    parent, snapshot, target, _ = replacement
    target["wwn"] = "50:00:00:00:00:00:00:01"
    assert identity.same_disk_identity(target, parent["target"])
    assert not node.prepare(request())["blocks"]


def test_shared_wwn_requires_independent_unique_serial_alias(replacement):
    _, snapshot, target, _ = replacement
    snapshot["disks"][0]["wwn"] = target["wwn"].upper()
    assert identity.composite_identity_verified(snapshot["disks"], target)
    plan = node.prepare(request())
    assert not plan["blocks"]
    assert any("WWN condiviso" in w for w in plan["warnings"])
    target["aliases"] = ["/dev/disk/by-id/wwn-synthetic"]
    assert not identity.composite_identity_verified(snapshot["disks"], target)
    assert node.prepare(request())["blocks"]


def test_duplicate_serial_and_duplicate_serial_alias_still_block(replacement):
    _, snapshot, target, _ = replacement
    other = snapshot["disks"][0]
    other["wwn"] = target["wwn"]
    other["aliases"].extend(target["aliases"])
    assert not identity.composite_identity_verified(snapshot["disks"], target)
    other["aliases"] = []
    other["serial"] = target["serial"]
    assert not identity.composite_identity_verified(snapshot["disks"], target)


def test_rebound_serial_alias_blocks_even_if_wwn_link_matches(monkeypatch):
    target = {"by_id": "/dev/disk/by-id/wwn-synthetic", "path": "/dev/sdz", "serial": "DEMO_REPLACEMENT",
              "aliases": ["/dev/disk/by-id/ata-DEMO_REPLACEMENT"], "wwn": "0x5000000000000001", "size": 1000}
    monkeypatch.setattr(node.os.path, "realpath", lambda p: "/dev/sdz" if p == target["by_id"] else "/dev/wrong")
    with pytest.raises(core.SafetyError, match="stesso dispositivo"):
        node.verify_target({"target": target})


@pytest.mark.parametrize("state", ["FAILED", "UNKNOWN"])
def test_failed_replace_observes_live_system_without_replay_or_changed_approval(replacement, monkeypatch, state):
    parent, snapshot, _, _ = replacement
    parent.update(state=state, error="historical command error")
    parent["events"][0].update(state="FAILED", code=1, command="zpool replace synthetic")
    node.save_record(parent)
    monkeypatch.setattr(node, "spawn_worker", lambda *a: pytest.fail("Never retry"))
    result = node.get_status(parent["id"])
    assert result["state"] == state
    assert result["events"] == parent["events"]
    assert result["system_observation"]["state"] == "ONLINE"
    assert workflow.should_refresh(result)
    # Current health is not proof that this failed command performed the replace.
    assert node.prepare(request())["blocks"]
    snapshot["pools"][0].update(state="DEGRADED", scan="resilver in progress since synthetic-date", scan_percent=21.5)
    result = node.get_status(parent["id"])
    assert result["system_observation"]["state"] == "RESILVERING"
    assert result["state"] == state
    snapshot["disks"][-1]["serial"] = "different"
    assert node.get_status(parent["id"])["system_observation"]["state"] == "UNCONFIRMED"


def test_rendered_diagnostics_escape_host_command_and_error_text(replacement):
    plan = node.prepare(request())
    plan["blocks"] = ["<script>unsafe</script>"]
    rendered = presentation.plan_html(plan, "<server>")
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    assert "&lt;server&gt;" in rendered
