"""Per-disk boot evidence and read-only ESP inspection.

No filesystem is repaired here. ESP contents are read in a private mount
namespace using ro,nodev,nosuid,noexec; no host mount is changed or reused.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import struct
import sys
import tempfile
from pathlib import Path

from .core import ESP, GOOD, SafetyError, disk_for_member, leaves, partition_for, digest


def parse_boot_entries(text):
    entries, current, order = [], "", []
    for line in text.splitlines():
        if line.startswith("BootCurrent:"):
            current = line.split(":", 1)[1].strip().upper()
        elif line.startswith("BootOrder:"):
            order = [s.strip().upper() for s in line.split(":", 1)[1].split(",")]
        match = re.match(r"Boot([0-9a-fA-F]{4})(\*)?\s+(.*)", line)
        if not match:
            continue
        part = re.search(r"HD\(\d+,GPT,([^,]+),", match[3], re.I)
        file = re.search(r"File\(([^)]+)\)", match[3], re.I)
        if part and file:
            entries.append({"id": match[1].upper(), "active": bool(match[2]),
                            "partuuid": part[1].lower(), "file": normalize_path(file[1])})
    return {"current": current, "order": order, "entries": entries}


def normalize_path(value):
    return str(value).replace("\\", "/").strip("/").casefold()


def configured_mode(status, uuid):
    modes = set()
    for line in status.splitlines():
        if not uuid or not re.match(r"^" + re.escape(uuid) + r"\s+is configured with:", line.strip(), re.I):
            continue
        if "configured with: grub" in line.lower():
            modes.add("grub")
        elif "configured with: uefi" in line.lower():
            modes.add("systemd")
    return modes.pop() if len(modes) == 1 else "unknown"


def efi_executable(data):
    """Structural PE/COFF check only, not signature validation or a boot test."""
    if len(data) < 64 or data[:2] != b"MZ":
        return False
    offset = struct.unpack_from("<I", data, 60)[0]
    if offset > 65536 or offset + 94 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        return False
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    magic = struct.unpack_from("<H", data, offset + 24)[0]
    subsystem = struct.unpack_from("<H", data, offset + 24 + 68)[0]
    return machine == 0x8664 and magic == 0x20B and subsystem == 10


def file_inventory(directory):
    files = []
    root = Path(directory)
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        for name in sorted(names):
            path = Path(base) / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            item = {"path": relative, "size": size}
            if relative.lower().endswith(".efi"):
                if size > 64 * 1024 * 1024:
                    raise SafetyError("File EFI troppo grande per la diagnostica limitata.")
                with path.open("rb") as stream:
                    data = stream.read(2 * 1024 * 1024)
                item["efi_valid"] = efi_executable(data)
                low = data.lower()
                item["loader_kind"] = ("grub" if b"grub" in low else "systemd" if b"systemd-boot" in low
                                       else "shim" if b"shim" in low else "unknown")
            if relative.lower().endswith((".efi", ".cfg", ".conf")):
                if size > 64 * 1024 * 1024:
                    raise SafetyError("File di configurazione troppo grande per la diagnostica.")
                checksum = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        checksum.update(chunk)
                item["sha256"] = checksum.hexdigest()
            files.append(item)
            if len(files) > 10000:
                raise SafetyError("Troppi file nell'ESP: diagnostica incompleta.")
    return files


def inspect_esp_namespace(path, run):
    """Invoked only in the helper's dedicated unshare --mount child."""
    if not stat.S_ISBLK(os.stat(path).st_mode):
        raise SafetyError("La sorgente ESP non è un dispositivo a blocchi.")
    probe = run(["lsblk", "--json", "--nodeps", "--output", "TYPE,PARTTYPE", path])
    rows = json.loads(probe["out"]).get("blockdevices", [])
    if probe["code"] or len(rows) != 1 or rows[0].get("type") != "part" or str(rows[0].get("parttype", "")).lower() != ESP:
        raise SafetyError("Tipo GPT della partizione EFI non verificato.")
    with tempfile.TemporaryDirectory(prefix="pve-esp-", dir="/run") as directory:
        mounted = run(["mount", "-t", "vfat", "-o", "ro,nodev,nosuid,noexec", path, directory])
        if mounted["code"]:
            raise SafetyError("Montaggio ESP in sola lettura non riuscito.")
        try:
            files = file_inventory(directory)
        finally:
            result = run(["umount", directory])
            if result["code"]:
                raise SafetyError("Smontaggio ESP fallito nella namespace privata.")
    return {"files": files, "inspection_ok": True}


def collect_boot(snapshot, run, helper):
    previous = snapshot.get("boot", {})
    registration_path = Path("/etc/kernel/proxmox-boot-uuids")
    try:
        registrations = [line.split("#", 1)[0].strip().upper() for line in registration_path.read_text().splitlines()]
        registrations = sorted(set(s for s in registrations if s))
        readable = True
    except OSError:
        registrations, readable = [], False
    firmware = "UEFI" if Path("/sys/firmware/efi").is_dir() else "BIOS"
    secure_boot = None
    if firmware == "UEFI":
        try:
            data = Path("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c").read_bytes()
            secure_boot = bool(data[4]) if len(data) >= 5 else None
        except OSError:
            pass
    nvram = run(["efibootmgr", "-v"]) if firmware == "UEFI" else {"code": 0, "out": "", "err": "BIOS Legacy"}
    boot = {**previous, "firmware_mode": firmware, "architecture": getattr(os.uname(), "machine", "unknown").lower(),
            "secure_boot": secure_boot, "registered_uuids": registrations, "registration_readable": readable,
            "nvram": {**parse_boot_entries(nvram["out"]), "code": nvram["code"], "error": nvram["err"]},
            "esps": {}, "grub_config_present": False, "grub_config_sha256": ""}
    grub_config = Path("/boot/grub/grub.cfg")
    try:
        boot["grub_config_present"] = grub_config.is_file() and grub_config.stat().st_size > 0
        if boot["grub_config_present"] and grub_config.stat().st_size <= 2 * 1024 * 1024:
            boot["grub_config_sha256"] = hashlib.sha256(grub_config.read_bytes()).hexdigest()
    except OSError:
        pass
    disks = []
    for pool in snapshot.get("pools", []):
        if pool.get("role") in {"BOOT", "MISTO"}:
            for member in leaves(pool):
                disk = disk_for_member(snapshot, member)
                if disk and disk not in disks:
                    disks.append(disk)
    for disk in disks:
        for part in disk.get("partitions", []):
            if part.get("parttype", "").lower() != ESP:
                continue
            path = part["path"]
            probe = run(["blkid", "-p", "-o", "export", path])
            tags = dict(line.split("=", 1) for line in probe["out"].splitlines() if "=" in line)
            wipe = run(["wipefs", "--json", path])
            try:
                signatures = json.loads(wipe["out"])["signatures"] if wipe["code"] == 0 else None
            except (ValueError, KeyError):
                signatures = None
            info = {"probe_code": probe["code"], "probe": tags, "signatures": signatures,
                    "fsck_code": None, "fsck_output": "", "files": [], "inspection_ok": False,
                    "inspection_error": "Filesystem FAT32 non verificato."}
            mounted = bool(part.get("mounts")) or any(
                str(m.get("source", "")).startswith("/dev/") and os.path.realpath(m["source"]) == os.path.realpath(path)
                for m in snapshot.get("mounts", []))
            if tags.get("TYPE") == "vfat" and tags.get("VERSION", "").upper() == "FAT32" and probe["code"] == 0:
                if mounted:
                    info["inspection_error"] = "ESP montata: controllo di coerenza differito, nessuno smontaggio automatico."
                else:
                    fsck = run(["fsck.fat", "-n", path])
                    info["fsck_code"], info["fsck_output"] = fsck["code"], fsck["out"] + fsck["err"]
                    if fsck["code"] == 0:
                        result = run(["unshare", "--mount", "--propagation", "private", sys.executable,
                                      helper, "--inspect-esp", path])
                        try:
                            contents = json.loads(result["out"])
                            if result["code"] or not contents.get("ok"):
                                raise ValueError("ESP non leggibile nella namespace privata.")
                            info.update(contents["result"])
                            info["inspection_error"] = ""
                        except (ValueError, KeyError):
                            info["inspection_error"] = "Ispezione dei file EFI in sola lettura non riuscita."
                    else:
                        info["inspection_error"] = "fsck.fat -n non ha confermato la coerenza FAT32."
            boot["esps"][path] = info
    return boot


def boot_report(snapshot, pool):
    """Report both boot members. Missing evidence is UNKNOWN, never success."""
    boot = snapshot.get("boot", {})
    results = []
    for member in leaves(pool):
        disk = disk_for_member(snapshot, member)
        checks = {}
        record = {"member_guid": member.get("guid", ""), "member_state": member["state"],
                  "serial": disk.get("serial", "") if disk else "", "by_id": disk.get("by_id", "") if disk else "",
                  "esp": None, "mode": "unknown", "checks": checks, "configuration_ready": False}

        def check(name, state, detail):
            checks[name] = {"state": state, "detail": detail}

        check("firmware", "OK" if boot.get("firmware_mode") == "UEFI" else "UNKNOWN",
              "Modalità firmware: " + boot.get("firmware_mode", "non rilevata") + "; BIOS Legacy richiede una procedura distinta.")
        if not disk:
            check("esp", "UNKNOWN", "Membro assente o disco fisico non identificato.")
            results.append(record)
            continue
        esps = [p for p in disk.get("partitions", []) if p.get("parttype", "").lower() == ESP]
        zfs_part = partition_for(disk, member["name"])
        if len(esps) != 1 or not zfs_part or esps[0]["number"] == zfs_part.get("number"):
            check("esp", "ERROR", "ESP non univoca, assente o coincidente con il membro ZFS.")
            results.append(record)
            continue
        part = esps[0]
        record["esp"] = part
        check("esp", "OK", "Partizione EFI distinta dalla partizione ZFS: " + part["path"])
        info = boot.get("esps", {}).get(part["path"], {})
        probe = info.get("probe", {})
        fat_ok = info.get("probe_code") == 0 and probe.get("TYPE") == "vfat" and probe.get("VERSION", "").upper() == "FAT32"
        fat_error = "probe_code" in info and (info["probe_code"] in {0, 2}) and not fat_ok
        check("fat32", "OK" if fat_ok else "ERROR" if fat_error else "UNKNOWN", "FAT32 rilevato direttamente da blkid -p." if fat_ok else "Firma FAT32 non confermata; FSTYPE vuoto da solo non prova una cancellazione.")
        check("filesystem", "OK" if info.get("fsck_code") == 0 else "UNKNOWN" if info.get("fsck_code") is None else "ERROR",
              "Controllo fsck.fat -n, nessuna riparazione automatica.")
        fs_uuid = probe.get("UUID", "").upper()
        mode = configured_mode(boot.get("status", ""), fs_uuid)
        record["mode"] = mode
        registered = fs_uuid and fs_uuid in boot.get("registered_uuids", []) and mode != "unknown"
        check("registration", "OK" if registered else "ERROR" if boot.get("registration_readable") else "UNKNOWN",
              "UUID presente nel registro e riconosciuto da proxmox-boot-tool." if registered else "Registrazione ESP o modalità bootloader non confermata.")
        uuid_ok = bool(fs_uuid and part.get("uuid", "").upper() == fs_uuid and part.get("partuuid"))
        check("identity", "OK" if uuid_ok else "ERROR" if fs_uuid else "UNKNOWN", "Confronto UUID rilevato, UUID inventario e PARTUUID GPT.")
        files = {normalize_path(f["path"]): f for f in info.get("files", [])}
        valid = {p: f for p, f in files.items() if f.get("efi_valid")}
        fallback = valid.get("efi/boot/bootx64.efi")
        if mode == "grub":
            loader = any(p.endswith("/grubx64.efi") for p in valid) or bool(fallback and fallback.get("loader_kind") == "grub")
        elif mode == "systemd":
            loader = "efi/systemd/systemd-bootx64.efi" in valid or bool(fallback and fallback.get("loader_kind") == "systemd")
        else:
            loader = False
        check("loader", "OK" if loader else "ERROR" if info.get("inspection_ok") else "UNKNOWN",
              "File del bootloader EFI con struttura PE/COFF x64 verificata; firme Secure Boot non certificate.")
        configs = [p for p, f in files.items() if f.get("size", 0) > 0 and
                   (p.endswith("grub.cfg") if mode == "grub" else p.startswith("loader/entries/") and p.endswith(".conf"))]
        config_ok = bool(configs) or (mode == "grub" and boot.get("grub_config_present", False))
        kernels = [p for p, f in files.items() if f.get("size", 0) > 0 and "/vmlinuz-" in p]
        kernel_ok = any(files.get(p.replace("/vmlinuz-", "/initrd.img-"), {}).get("size", 0) > 0 for p in kernels)
        check("configuration", "OK" if config_ok and kernel_ok else "ERROR" if info.get("inspection_ok") else "UNKNOWN",
              "Configurazione del loader e almeno una coppia kernel/initrd nella ESP.")
        nvram = boot.get("nvram", {})
        matching = [e for e in nvram.get("entries", []) if str(e.get("partuuid", "")).casefold() == part.get("partuuid", "").casefold()
                    and e.get("active") and e.get("file") in valid and (e["id"] in nvram.get("order", []) or e["id"] == nvram.get("current"))]
        path_ok = bool(fallback or matching)
        check("uefi_path", "OK" if path_ok else "ERROR" if info.get("inspection_ok") else "UNKNOWN",
              "Fallback EFI valido sul disco." if fallback else "Voce UEFI attiva coerente con PARTUUID e file." if matching else "Nessun percorso UEFI utilizzabile confermato.")
        record["fallback_present"] = bool(fallback)
        record["nvram_entry_ids"] = [e["id"] for e in matching]
        if boot.get("secure_boot") is True and mode == "systemd":
            check("secure_boot", "UNKNOWN", "systemd-boot con Secure Boot non è certificabile da questa procedura.")
        record["evidence_digest"] = digest({"uuid": fs_uuid, "partuuid": part.get("partuuid"), "mode": mode,
                                            "files": sorted(info.get("files", []), key=lambda f: f["path"]), "secure_boot": boot.get("secure_boot"),
                                            "grub_config": boot.get("grub_config_sha256", "") if mode == "grub" else ""})
        record["configuration_ready"] = bool(checks) and all(c["state"] == "OK" for c in checks.values())
        record["state"] = "OK" if record["configuration_ready"] else "ERROR" if any(c["state"] == "ERROR" for c in checks.values()) else "UNKNOWN"
        results.append(record)
    ready = len(results) == 2 and all(r["configuration_ready"] for r in results)
    return {"firmware_mode": boot.get("firmware_mode", "UNKNOWN"), "secure_boot": boot.get("secure_boot"),
            "disks": results, "configuration_ready": ready, "autonomous_boot": "NON_TESTATO",
            "nvram_readable": boot.get("nvram", {}).get("code") == 0,
            "warnings": [line.strip() for line in boot.get("status", "").splitlines() if "WARN:" in line or "ERROR:" in line]}
