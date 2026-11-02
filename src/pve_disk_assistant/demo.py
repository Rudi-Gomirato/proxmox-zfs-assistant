"""Synthetic data, no real addresses or serials; demo never invokes SSH."""
from copy import deepcopy
from datetime import datetime, timezone

from .core import BIOS, ESP, classify, merge_guids, parse_status, storage_config
from . import __version__

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


def snapshot(raidz=False, boot_fault=False):
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
                    "number": number, "parttype": ptype, "fstype": fstype, "uuid": ("ABCD-1234" if name == "BOOT_A" else "ABCD-5678") if number == 2 else "",
                    "partuuid": f"demo-{name}-{number}", "size": 500_000_000_000 if number == 3 else 512_000_000})
        disks.append(disk)
    result = classify({"schema": 2, "demo": True, "helper_version": __version__, "host_id": "DEMO-HOST",
        "boot_id": "DEMO-SESSION-BEFORE", "boot_started_at": 1000,
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
    add_boot_observations(result, "grub" if boot_fault else "systemd")
    if boot_fault:
        part = result["disks"][0]["partitions"][1]
        part.update(uuid="", fstype="")
        result["boot"]["esps"][part["path"]] = {"probe_code": 2, "probe": {}, "signatures": [],
            "fsck_code": None, "files": [], "inspection_ok": False, "inspection_error": "Firma FAT32 non rilevata."}
        result["boot"]["registered_uuids"] = ["ABCD-5678", "FFFF-0000"]
        result["boot"]["status"] = "WARN: UUID precedente non disponibile\nABCD-5678 is configured with: grub (versions: demo)\n"
    return result


def add_boot_observations(snapshot, mode="systemd"):
    registered, esps, lines = [], {}, []
    for disk in snapshot["disks"]:
        for part in disk.get("partitions", []):
            if part.get("parttype") != ESP:
                continue
            part.setdefault("mounts", [])
            fs_uuid = part["uuid"]
            registered.append(fs_uuid)
            lines.append(f"{fs_uuid} is configured with: {'uefi' if mode == 'systemd' else 'grub'} (versions: demo)")
            binary = "EFI/systemd/systemd-bootx64.efi" if mode == "systemd" else "EFI/proxmox/grubx64.efi"
            files = [{"path": p, "size": 1024, "efi_valid": True, "loader_kind": mode}
                     for p in (binary, "EFI/BOOT/BOOTX64.EFI")]
            files += [{"path": p, "size": 4096} for p in ("loader/entries/proxmox-demo.conf" if mode == "systemd" else "EFI/proxmox/grub.cfg",
                        "EFI/proxmox/demo/vmlinuz-demo", "EFI/proxmox/demo/initrd.img-demo")]
            esps[part["path"]] = {"probe_code": 0, "probe": {"TYPE": "vfat", "VERSION": "FAT32", "UUID": fs_uuid},
                "signatures": [{"type": "vfat"}], "fsck_code": 0, "fsck_output": "Dati sintetici", "inspection_ok": True, "files": files}
    snapshot["boot"] = {"firmware_mode": "UEFI", "architecture": "x86_64", "secure_boot": False,
        "registered_uuids": registered, "registration_readable": True, "status": "\n".join(lines), "code": 0,
        "nvram": {"code": 0, "current": "0001", "order": ["0001"], "entries": []}, "esps": esps, "grub_config_present": True}


class DemoBackend:
    def __init__(self, raidz=False, boot_fault=False):
        self.raidz = raidz
        self.boot_fault = boot_fault

    def rpc(self, request):
        from .core import plan_replacement
        if request["op"] == "collect":
            return snapshot(self.raidz, self.boot_fault)
        if request["op"] == "plan":
            result = plan_replacement(snapshot(self.raidz, self.boot_fault), request["request"])
            result.update({"id": "demo", "state": "PREVIEW", "events": []})
            return result
        if request["op"] == "list_jobs":
            return []
        raise RuntimeError("Modalità dimostrativa: nessuna modifica o identificazione hardware reale.")
