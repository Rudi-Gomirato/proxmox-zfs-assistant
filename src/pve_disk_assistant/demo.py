"""Synthetic data, no real addresses or serials; demo never invokes SSH."""
from copy import deepcopy
from datetime import datetime, timezone

from .core import BIOS, ESP, classify, merge_guids, parse_status, storage_config

STATUS = """  pool: rpool
 state: ONLINE
  scan: scrub repaired 0B with 0 errors
config:

        NAME                                      STATE     READ WRITE CKSUM
        rpool                                     ONLINE       0     0     0
          mirror-0                                ONLINE       0     0     0
            /dev/disk/by-id/ata-DEMO_BOOT_A-part3   ONLINE       0     0     0
            /dev/disk/by-id/ata-DEMO_BOOT_B-part3   ONLINE       0     0     0

errors: No known data errors

  pool: vmdata
 state: DEGRADED
  scan: none requested
config:

        NAME                                      STATE     READ WRITE CKSUM
        vmdata                                    DEGRADED     0     0     0
          mirror-0                                DEGRADED     0     0     0
            /dev/disk/by-id/ata-DEMO_VM_A           ONLINE       0     0     0
            202                                    UNAVAIL      0     0     0  was /dev/disk/by-id/ata-DEMO_VM_B

errors: No known data errors
"""


def snapshot(raidz=False):
    text = STATUS
    if raidz:
        # Only change the VM group, leaving the boot mirror intact.
        first, second = text.split("  pool: vmdata")
        second = second.replace("mirror-0", "raidz1-0").replace("            202", "            /dev/disk/by-id/ata-DEMO_VM_C           ONLINE       0     0     0\n            202")
        text = first + "  pool: vmdata" + second
    guid_text = text
    for name, guid in (("/dev/disk/by-id/ata-DEMO_BOOT_A-part3", "101"),
                       ("/dev/disk/by-id/ata-DEMO_BOOT_B-part3", "102"),
                       ("/dev/disk/by-id/ata-DEMO_VM_A", "201"),
                       ("/dev/disk/by-id/ata-DEMO_VM_C", "203")):
        guid_text = guid_text.replace(name, guid)
    pools = parse_status(text)
    merge_guids(pools, guid_text, {"rpool": "1000", "vmdata": "2000"})
    disks = []
    for index, name in enumerate(["BOOT_A", "BOOT_B", "VM_A", "NEW"] + (["VM_C"] if raidz else [])):
        path, by_id = "/dev/sd" + chr(97 + index), "/dev/disk/by-id/ata-DEMO_" + name
        disk = {"path": path, "by_id": by_id, "aliases": [by_id], "serial": "DEMO_" + name,
                "model": "SSD laboratorio", "wwn": "", "size": 512_110_190_592, "log_sec": 512,
                "phy_sec": 4096, "ro": False, "mounts": [], "partitions": [], "holders": [],
                "signatures": [] if name == "NEW" else [{"type": "zfs_member"}],
                "signatures_checked": True, "identity_verified": True, "locate_paths": [],
                "smart": {"passed": True, "exit_code": 0, "temperature": 32}, "table": {}}
        if name.startswith("BOOT"):
            disk["table"] = {"label": "gpt"}
            disk["signatures"] = [{"type": "gpt"}]
            for number, ptype, fstype in ((1, BIOS, ""), (2, ESP, "vfat"), (3, "6a898cc3-1dd2-11b2-99a6-080020736631", "zfs_member")):
                disk["partitions"].append({"path": path + str(number), "aliases": [by_id + "-part" + str(number)],
                    "number": number, "parttype": ptype, "fstype": fstype, "uuid": "ABCD-1234" if number == 2 else "",
                    "partuuid": f"demo-{name}-{number}", "size": 500_000_000_000 if number == 3 else 512_000_000})
        disks.append(disk)
    return classify({"schema": 1, "demo": True, "helper_version": "0.1.0", "host_id": "DEMO-HOST",
        "hostname": "pve-laboratorio", "collected_at": datetime.now(timezone.utc).isoformat(),
        "pve_version": "pve-manager/9.0.0/demo", "root": {"fstype": "zfs", "source": "rpool/ROOT/pve-1", "target": "/"},
        "mounts": [], "pools": pools, "disks": disks,
        "storages": storage_config("zfspool: local-zfs\n\tpool rpool/data\nzfspool: vm-zfs\n\tpool vmdata/vm\n"),
        "guests": [{"id": "100", "name": "Gestionale", "kind": "VM", "storages": ["vm-zfs"]},
                   {"id": "101", "name": "Backup", "kind": "VM", "storages": ["vm-zfs"]}],
        "boot": {"status": "ABCD-1234 is configured with: uefi (versions: demo)\n", "code": 0},
        "tools": ["zpool", "zfs", "sgdisk", "partprobe", "udevadm", "proxmox-boot-tool"],
        "issues": [], "complete": True, "raw": {"status": {"out": text}},
        "guest_scope": "Dati sintetici del solo nodo dimostrativo"})


class DemoBackend:
    def __init__(self, raidz=False):
        self.raidz = raidz

    def rpc(self, request):
        from .core import plan_replacement
        if request["op"] == "collect":
            return snapshot(self.raidz)
        if request["op"] == "plan":
            result = plan_replacement(snapshot(self.raidz), request["request"])
            result.update({"id": "demo", "state": "PREVIEW", "events": []})
            return result
        if request["op"] == "list_jobs":
            return []
        raise RuntimeError("Modalità dimostrativa: nessuna modifica o identificazione hardware reale.")
