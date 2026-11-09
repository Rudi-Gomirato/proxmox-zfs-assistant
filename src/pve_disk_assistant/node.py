"""Linux-only, standard-library helper. Requests are JSON over stdin/stdout.

Installed explicitly as a root-owned zipapp. No request supplies shell code or
arbitrary command arguments. Destructive commands come only from core planning.
"""
from __future__ import annotations

import contextlib
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .boot import boot_report, collect_boot, inspect_esp_namespace
from .esp import blank_esp
from .core import (SafetyError, classify, data_groups, digest, disk_for, leaves,
                   merge_guids, parse_status, plan_replacement, safety_fingerprint, topology_fingerprint, ESP, disk_for_member)

STATE = Path("/var/lib/pve-disk-assistant")
HELPER = "/usr/local/lib/pve-disk-assistant/helper.pyz"
PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
TOOLS = ("zpool", "zfs", "lsblk", "findmnt", "wipefs", "smartctl", "sfdisk", "sgdisk",
         "partprobe", "udevadm", "pveversion", "proxmox-boot-tool", "shutdown", "blkid", "fsck.fat",
         "mount", "umount", "unshare", "efibootmgr")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def run(argv, timeout=40):
    executable = shutil.which(argv[0], path=PATH)
    if not executable:
        return {"code": 127, "out": "", "err": "Strumento non disponibile: " + argv[0]}
    try:
        proc = subprocess.run([executable, *argv[1:]], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env={"PATH": PATH, "LC_ALL": "C", "LANG": "C", "HOME": "/root"}, shell=False)
        return {"code": proc.returncode, "out": proc.stdout[:8_000_000], "err": proc.stderr[:32000]}
    except subprocess.TimeoutExpired as exc:
        # This is an unknown outcome for writes, never an invitation to retry.
        raise SafetyError("Timeout; esito da verificare sul nodo: " + argv[0]) from exc


def read_text(path):
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return ""


def json_output(result, fallback=None):
    try:
        return json.loads(result["out"])
    except (ValueError, TypeError):
        return fallback if fallback is not None else {}


def flatten_mounts(nodes):
    result = []
    for node in nodes:
        result.append({k: node.get(k, "") for k in ("source", "fstype", "target")})
        result.extend(flatten_mounts(node.get("children", [])))
    return result


def aliases():
    mapping = {}
    for base in ("/dev/disk/by-id", "/dev/disk/by-partuuid", "/dev/disk/by-uuid"):
        for path in glob.glob(base + "/*"):
            if os.path.exists(path):
                mapping.setdefault(os.path.realpath(path), []).append(path)
    return mapping


def stable_id(paths):
    paths = [p for p in paths if p.startswith("/dev/disk/by-id/")
             and re.match(r"(?:ata-|wwn-|scsi-|nvme-)", os.path.basename(p))
             and not re.search(r"-part\d+$", p)]
    return sorted(paths, key=lambda p: (not os.path.basename(p).startswith("wwn-"), p))[0] if paths else ""


def discover_disks(blocks):
    mapping = aliases()
    disks, seen = [], set()

    def descend(nodes):
        for n in nodes:
            yield n
            yield from descend(n.get("children", []))

    for item in descend(blocks):
        if item.get("type") != "disk" or item["name"] in seen:
            continue
        seen.add(item["name"])
        path = item["name"]
        serial = str(item.get("serial") or "").strip()
        disk = {"path": path, "serial": serial, "model": str(item.get("model") or "").strip(),
                "wwn": item.get("wwn") or "", "size": int(item.get("size") or 0),
                "log_sec": int(item.get("log-sec") or 0), "phy_sec": int(item.get("phy-sec") or 0),
                "ro": bool(item.get("ro")), "aliases": mapping.get(path, []),
                "mounts": [m for m in item.get("mountpoints", []) or [] if m],
                "partitions": [], "holders": [], "signatures": [], "smart": {}}
        disk["by_id"] = stable_id(disk["aliases"])
        disk["identity_verified"] = bool(disk["by_id"] and os.path.realpath(disk["by_id"]) == path and serial)
        children = list(descend(item.get("children", [])))
        for child in children:
            if child.get("type") == "part":
                ppath = child["name"]
                disk["partitions"].append({"path": ppath, "aliases": mapping.get(ppath, []),
                    "number": int(read_text("/sys/class/block/" + os.path.basename(ppath) + "/partition") or "0"),
                    "size": int(child.get("size") or 0), "parttype": child.get("parttype") or "",
                    "uuid": child.get("uuid") or "", "partuuid": child.get("partuuid") or "",
                    "fstype": child.get("fstype") or "", "mounts": [m for m in child.get("mountpoints", []) or [] if m]})
            disk["mounts"].extend(m for m in child.get("mountpoints", []) or [] if m)
            if child.get("type") not in {"part"}:
                disk["holders"].append(child["name"])
        for device in [path, *[p["path"] for p in disk["partitions"]]]:
            disk["holders"].extend(glob.glob("/sys/class/block/" + os.path.basename(device) + "/holders/*"))
        sig = run(["wipefs", "--json", path])
        sig_json = json_output(sig)
        disk["signatures_checked"] = sig["code"] == 0 and isinstance(sig_json.get("signatures"), list)
        disk["signatures"] = sig_json.get("signatures", [])
        disk["table"] = json_output(run(["sfdisk", "--json", path])).get("partitiontable", {})
        if shutil.which("smartctl", path=PATH):
            smart_result = run(["smartctl", "-a", "-j", path], timeout=15)
            sj = json_output(smart_result)
            disk["smart"] = {"passed": sj.get("smart_status", {}).get("passed"),
                             "exit_code": smart_result["code"],
                             "temperature": sj.get("temperature", {}).get("current"), "raw": sj}
        disk["locate_paths"] = [p for p in glob.glob("/sys/class/enclosure/*/*/locate")
                                if os.path.exists(str(Path(p).parent / "device" / "block" / os.path.basename(path)))]
        disks.append(disk)
    return disks


def collect():
    issues, raw = [], {}

    def essential(name, argv):
        result = run(argv)
        raw[name] = result
        if result["code"] != 0:
            issues.append(name + ": " + result["err"])
        return result

    version = essential("version", ["pveversion"])
    named = essential("status", ["zpool", "status", "-P", "-p"])
    guid = essential("guids", ["zpool", "status", "-g", "-P", "-p"])
    confirm = essential("status_confirm", ["zpool", "status", "-P", "-p"])
    props = essential("pool_guids", ["zpool", "get", "-H", "-o", "name,value", "guid"])
    pools = parse_status(named["out"])
    propmap = dict(line.split("\t", 1) for line in props["out"].splitlines() if "\t" in line)
    merge_guids(pools, guid["out"], propmap)
    # Compare only topology and error counters; scan progress may legitimately move.
    def tree_shape(pool):
        return {"name": pool["name"], "state": pool["state"], "children": pool["children"]}
    if [tree_shape(p) for p in parse_status(named["out"])] != [tree_shape(p) for p in parse_status(confirm["out"])]:
        issues.append("Topologia o contatori cambiati durante la raccolta; ripetere l'analisi.")
    ls = essential("lsblk", ["lsblk", "--json", "--bytes", "--paths", "--output",
        "NAME,TYPE,SIZE,MODEL,SERIAL,WWN,LOG-SEC,PHY-SEC,FSTYPE,MOUNTPOINTS,PARTTYPE,PARTUUID,UUID,RO"])
    lb = json_output(ls)
    if "blockdevices" not in lb:
        issues.append("Inventario lsblk non decodificabile.")
    mounts_result = essential("findmnt", ["findmnt", "--json", "--output", "SOURCE,FSTYPE,TARGET"])
    mounts = flatten_mounts(json_output(mounts_result).get("filesystems", []))
    root = next((m for m in mounts if m["target"] == "/"), {})
    if not root:
        issues.append("Filesystem root non identificato.")
    from .core import storage_config
    config_path = Path("/etc/pve/storage.cfg")
    config = read_text(config_path)
    if not config:
        issues.append("Configurazione storage Proxmox non leggibile.")
    stores = storage_config(config)
    guests = []
    for kind, pattern in (("VM", "/etc/pve/qemu-server/*.conf"), ("CT", "/etc/pve/lxc/*.conf")):
        for path in sorted(glob.glob(pattern)):
            content = read_text(path)
            name = re.search(r"^(?:name|hostname):\s*(.*)$", content, re.M)
            ids = sorted(set(re.findall(r"(?:^|\s|,)([A-Za-z0-9_-]+):[^\s,]+", content, re.M)) & {s["id"] for s in stores})
            guests.append({"id": Path(path).stem, "name": name[1] if name else "", "kind": kind, "storages": ids})
    boot = run(["proxmox-boot-tool", "status"])
    boot_started = re.search(r"^btime (\d+)$", read_text("/proc/stat"), re.M)
    snapshot = {"schema": 2, "helper_version": __version__, "collected_at": utcnow(),
        "boot_id": read_text("/proc/sys/kernel/random/boot_id"), "boot_started_at": int(boot_started[1]) if boot_started else 0,
        "host_id": read_text("/etc/machine-id"), "hostname": os.uname().nodename.split(".")[0],
        "pve_version": version["out"].splitlines()[0] if version["out"] else "",
        "root": root, "mounts": mounts, "pools": pools, "disks": discover_disks(lb.get("blockdevices", [])),
        "storages": stores, "guests": guests, "boot": {"status": boot["out"] + boot["err"], "code": boot["code"]},
        "tools": [t for t in TOOLS if shutil.which(t, path=PATH)], "issues": issues,
        "complete": not issues, "raw": raw, "guest_scope": "Configurazioni VM/CT del solo nodo collegato"}
    if not pools:
        snapshot["issues"].append("Nessun pool ZFS importato identificato.")
        snapshot["complete"] = False
    classify(snapshot)
    metrics = run(['zpool', 'list', '-Hp', '-o', 'name,size,alloc,free,cap,frag,health'])
    snapshot['pool_metrics'] = {}
    if metrics['code'] == 0:
        for line in metrics['out'].splitlines():
            values = line.split('\t')
            if len(values) == 7:
                snapshot['pool_metrics'][values[0]] = dict(zip(('size', 'allocated', 'free', 'capacity_percent', 'fragmentation_percent', 'health'), values[1:]))
    snapshot['hardware_notes'] = ['Possibile volume RAID/virtuale: ' + d['path'] + '; verificare controller e accesso SMART ai dischi fisici.'
        for d in snapshot['disks'] if re.search(r'logical|virtual disk|raid', d.get('model', ''), re.I)]
    snapshot["boot"] = collect_boot(snapshot, run, HELPER)
    return snapshot


def state_dir():
    if os.geteuid() != 0:
        raise SafetyError("L'helper operativo richiede root. Configurare l'accesso come descritto nel manuale.")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = STATE.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077:
        raise SafetyError("Directory di stato non protetta o non appartenente a root.")
    return STATE


def record_path(identifier):
    if not re.fullmatch(r"[0-9a-f]{32}", str(identifier)):
        raise SafetyError("Identificativo intervento non valido.")
    return state_dir() / (identifier + ".json")


def save_record(record):
    path = record_path(record["id"])
    fd, temporary = tempfile.mkstemp(prefix=record["id"] + "-", suffix=".tmp", dir=state_dir())
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_record(identifier):
    path = record_path(identifier)
    if not path.exists():
        raise SafetyError("Intervento non trovato sul nodo.")
    return json.loads(path.read_text())


@contextlib.contextmanager
def locked(name="operation"):
    import fcntl
    with (state_dir() / (name + ".lock")).open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SafetyError("Un altro intervento sta operando sul nodo.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def initialization_evidence(snapshot, request):
    """Derive provenance exclusively from the root-owned node journal."""
    plan = plan_replacement(snapshot, request)
    target, esp = plan["target"], plan.get("expected_esp") or {}
    evidence = {}
    try:
        matches = []
        for path in state_dir().glob("*.json"):
            old = json.loads(path.read_text())
            if request.get("replacement_id") and old.get("id") != request["replacement_id"]:
                continue
            if (old.get("action") == "replace" and old.get("host_id") == snapshot.get("host_id")
                    and old.get("pool_guid") == plan["pool_guid"]
                    and old.get("role") in {"BOOT", "MISTO"}
                    and all(target.get(k) and old.get("target", {}).get(k) == target[k] for k in ("by_id", "serial", "wwn"))
                    and esp.get("partuuid") and old.get("expected_esp", {}).get("partuuid", "").lower() == esp["partuuid"].lower()
                    and any(e.get("phase") == "replace" and e.get("state") == "OK" and e.get("code") == 0 for e in old.get("events", []))):
                matches.append(old)
        if len(matches) != 1:
            raise SafetyError("Registro della sostituzione non univoco o identità WWN/seriale/PARTUUID non corrispondente. Nessuna formattazione.")
        old = matches[0]
        if old.get("state") in {"QUEUED", "RUNNING", "UNKNOWN"}:
            raise SafetyError("Sostituzione ancora attiva o con esito ignoto: verificare l'intervento prima della ESP.")
        evidence["replacement_id"] = old["id"]
        evidence["identity"] = {"by_id": target["by_id"], "serial": target["serial"],
                                "wwn": target["wwn"], "partuuid": esp["partuuid"]}
        boot_resume_checks(plan, snapshot)
        pool = next(p for p in snapshot["pools"] if p["guid"] == plan["pool_guid"])
        old_pool = next((p for p in old.get("before", {}).get("pools", []) if p["guid"] == plan["pool_guid"]), {})
        if (not re.search(r"(?:resilvered|scrub repaired).*with 0 errors", pool.get("scan", ""), re.S)
                or pool.get("scan") == old_pool.get("scan")):
            raise SafetyError("Manca l'esito di resilver (o successivo scrub) concluso senza errori dopo la sostituzione.")
        if target.get("holders") or esp.get("mounts"):
            raise SafetyError("ESP in uso o dispositivi dipendenti presenti.")
        evidence["blank"] = blank_esp(plan["expected_esp"]["path"], esp.get("size", 0), run)
    except (SafetyError, OSError, ValueError) as exc:
        evidence["error"] = str(exc)
    return evidence


def build_plan(snapshot, request):
    if request.get("action") == "initialize_esp":
        snapshot = {**snapshot, "esp_initialization": initialization_evidence(snapshot, request)}
    return plan_replacement(snapshot, request)


def same_plan(before, after):
    return (not after["blocks"] and before["fingerprint"] == after["fingerprint"]
            and before.get("initialization_evidence") == after.get("initialization_evidence"))


def prepare(request):
    snapshot = collect()
    plan = build_plan(snapshot, request)
    if plan.get("replacement_id"):
        plan["request"]["replacement_id"] = plan["replacement_id"]
    import getpass
    plan["operator"] = os.environ.get("SUDO_USER") or getpass.getuser()
    plan["before"] = snapshot
    plan["id"] = uuid.uuid4().hex
    plan["created_at"] = time.time()
    plan["helper_version"] = __version__
    plan["approval_digest"] = digest(plan)
    plan["state"] = "PREVIEW"
    plan["events"] = []
    save_record(plan)
    return plan


def spawn_worker(identifier, mode="--run"):
    log = (state_dir() / (identifier + ".log")).open("ab")
    try:
        return subprocess.Popen([sys.executable, HELPER, mode, identifier], stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True, close_fds=True).pid
    finally:
        log.close()


def submit(request):
    with locked():
        plan = load_record(request.get("id"))
        if plan["state"] != "PREVIEW":
            raise SafetyError("Piano già inviato: consultare l'esito, non inviarlo di nuovo.")
        if plan.get("helper_version") != __version__:
            raise SafetyError("Piano creato con una versione precedente dell'helper: generare una nuova anteprima.")
        if plan["blocks"]:
            raise SafetyError("Piano bloccato: " + "; ".join(plan["blocks"]))
        if time.time() - plan["created_at"] > 600:
            raise SafetyError("Piano scaduto. Generare una nuova anteprima.")
        if (request.get("approval_digest") != plan["approval_digest"] or
                request.get("serial_confirmation") != plan["target"]["serial"] or
                not request.get("backup_confirmed") or not request.get("physical_confirmed")):
            raise SafetyError("Approvazione, seriale o conferme mancanti.")
        if plan.get("action") == "initialize_esp":
            if request.get("format_partuuid_confirmation") != plan["expected_esp"]["partuuid"]:
                raise SafetyError("Conferma esplicita della formattazione e PARTUUID mancanti.")
            plan["format_approved"] = {"partuuid": request["format_partuuid_confirmation"], "at": utcnow()}
        # A queued worker may not yet own the operation lock.
        for path in state_dir().glob("*.json"):
            entry = json.loads(path.read_text())
            if entry["id"] == plan.get("replacement_id") and entry.get("state") == "WAITING_RESILVER":
                continue  # Fresh ONLINE/source/identity checks below remain mandatory.
            if entry["id"] != plan["id"] and entry.get("state") in {"QUEUED", "RUNNING", "WAITING_RESILVER", "MONITORING", "UNKNOWN"}:
                raise SafetyError("Un intervento è già in coda, attivo o con esito ignoto.")
        fresh = collect()
        refreshed = build_plan(fresh, plan["request"])
        if not same_plan(plan, refreshed):
            raise SafetyError("Inventario cambiato dopo l'anteprima. Analizzare e approvare un nuovo piano.")
        plan["state"], plan["approved_at"] = "QUEUED", utcnow()
        save_record(plan)
        if plan.get("replacement_id"):
            parent = load_record(plan["replacement_id"])
            parent.update(state="ESP_INITIALIZATION_LINKED", initialization_job=plan["id"],
                          result_message="Fase EFI affidata all'intervento " + plan["id"] + ". Consultarlo per esiti e prove di avvio.")
            save_record(parent)
        # Worker waits briefly for this submission lock to be released.
        try:
            spawn_worker(plan["id"])
        except Exception:
            plan["state"] = "FAILED_TO_START"
            save_record(plan)
            raise
        return {"id": plan["id"], "state": plan["state"]}


def verify_target(plan):
    target = plan["target"]
    if os.path.realpath(target["by_id"]) != target["path"]:
        raise SafetyError("La destinazione persistente ha cambiato dispositivo: piano interrotto.")
    result = run(["lsblk", "--json", "--bytes", "--nodeps", "--output", "NAME,SERIAL,WWN,SIZE,RO", target["by_id"]])
    rows = json_output(result).get("blockdevices", [])
    if (result["code"] or len(rows) != 1 or str(rows[0].get("serial") or "").strip() != target["serial"]
            or int(rows[0].get("size") or 0) != target["size"] or rows[0].get("ro")
            or (target.get("wwn") and rows[0].get("wwn") != target["wwn"])):
        raise SafetyError("Seriale, capacità o accessibilità del nuovo disco cambiati.")


def verify_partition(plan, path):
    target_path = plan["target"]["path"]
    real = os.path.realpath(path)
    sys_path = Path("/sys/class/block") / os.path.basename(real)
    if not sys_path.exists() or not (sys_path / "partition").exists():
        raise SafetyError("Partizione nuova non rilevata: " + path)
    parent_name = sys_path.resolve().parent.name
    if "/dev/" + parent_name != target_path:
        raise SafetyError("La partizione non appartiene al nuovo disco approvato.")


def verify_copied_layout(plan):
    source = plan.get("source")
    if not source:
        return
    new = json_output(run(["sfdisk", "--json", plan["target"]["by_id"]])).get("partitiontable", {})
    original = source.get("table", {})
    def shape(table):
        return [(p.get("start"), p.get("size"), p.get("type", "").lower()) for p in table.get("partitions", [])]
    if (new.get("label") != "gpt" or not shape(original) or shape(new) != shape(original)
            or new.get("id") == original.get("id")
            or any(a.get("uuid") == b.get("uuid") for a, b in zip(new.get("partitions", []), original.get("partitions", [])))):
        raise SafetyError("GPT nuova non coerente con il riferimento o GUID non rigenerati.")


def bind_esp(plan):
    format_or_init = next((s for s in plan["steps"] if s["argv"][:2] in (
        ["proxmox-boot-tool", "format"], ["proxmox-boot-tool", "init"])), None)
    if not format_or_init and not plan.get("planned_esp_path"):
        raise SafetyError("Nessuna ESP destinataria nel piano di boot.")
    path = format_or_init["argv"][2] if format_or_init else plan["planned_esp_path"]
    verify_partition(plan, path)
    result = run(["lsblk", "--json", "--nodeps", "--output", "TYPE,PARTTYPE,PARTUUID,MOUNTPOINTS", path])
    rows = json_output(result).get("blockdevices", [])
    if (result["code"] or len(rows) != 1 or rows[0].get("type") != "part"
            or str(rows[0].get("parttype", "")).lower() != ESP or not rows[0].get("partuuid")
            or any(rows[0].get("mountpoints", []) or [])):
        raise SafetyError("ESP destinataria non verificata come partizione GPT EFI libera da mount.")
    actual = {"path": path, "partuuid": rows[0]["partuuid"].lower()}
    expected = plan.get("expected_esp")
    if expected and expected.get("partuuid", "").lower() != actual["partuuid"]:
        raise SafetyError("PARTUUID della ESP selezionata cambiato: nessuna formattazione.")
    if os.path.realpath(path) == os.path.realpath(plan["new_member"]):
        raise SafetyError("La ESP coincide con il membro ZFS: operazione vietata.")
    plan["expected_esp"] = {**(expected or {}), **actual}


def verify_boot_write(plan, step):
    if step["phase"] != "boot":
        return
    bind_esp(plan)
    path = step["argv"][2]
    if path != plan["expected_esp"]["path"]:
        raise SafetyError("Comando riferito a una ESP diversa dal bersaglio approvato.")
    if step["argv"][1] == "format":
        if (plan.get("action") != "initialize_esp" or
                plan.get("format_approved", {}).get("partuuid") != plan["expected_esp"].get("partuuid")):
            raise SafetyError("Formattazione annullata: occorre il piano distinto di inizializzazione e la conferma del PARTUUID.")
        checked = build_plan(collect(), plan["request"])
        if not same_plan(plan, checked):
            raise SafetyError("Verifica immediata ESP fallita: formattazione annullata. " + "; ".join(checked["blocks"]))
    elif plan.get("action") == "initialize_esp":
        fresh = collect()
        boot_resume_checks(plan, fresh)
        pool = next(p for p in fresh["pools"] if p["guid"] == plan["pool_guid"])
        row = next(r for r in boot_report(fresh, pool)["disks"] if r["by_id"] == plan["target"]["by_id"])
        if any(row["checks"][k]["state"] != "OK" for k in ("fat32", "filesystem", "identity")):
            raise SafetyError("FAT32 o UUID appena creati non verificati: init annullato.")
        if sum(p.get("uuid", "").upper() == row["esp"]["uuid"].upper()
               for d in fresh["disks"] for p in d.get("partitions", [])) != 1:
            raise SafetyError("UUID FAT32 duplicato: init annullato.")
        plan["formatted_uuid"] = row["esp"]["uuid"]


def boot_resume_checks(plan, snapshot):
    verify_target(plan)
    pool = next((p for p in snapshot["pools"] if p["guid"] == plan["pool_guid"]), None)
    if not pool or not zfs_ready(plan, snapshot, pool):
        raise SafetyError("Il resilver non è concluso o il mirror non è verificato.")
    report = boot_report(snapshot, pool)
    source = plan.get("source") or {}
    if not any(r["serial"] == source.get("serial") and r["configuration_ready"] for r in report["disks"]):
        raise SafetyError("Il membro di riferimento non ha più una configurazione EFI verificata.")
    bind_esp(plan)


def zfs_ready(plan, snapshot, pool):
    target = next((d for d in snapshot["disks"] if d.get("by_id") == plan["target"]["by_id"]
                   and d.get("serial") == plan["target"]["serial"]), None)
    members = leaves(pool)
    return (snapshot["complete"] and not snapshot.get("issues") and not pool.get("warnings") and target is not None
            and (not plan["target"].get("wwn") or target.get("wwn") == plan["target"]["wwn"])
            and any(disk_for_member(snapshot, m) == target for m in members)
            and pool["state"] == "ONLINE" and pool["errors"] == "No known data errors"
            and all(m["state"] in {"ONLINE", "HEALTHY"} and len(m["errors"]) == 3 and not any(m["errors"]) for m in members)
            and "in progress" not in pool["scan"])


def perform_job(identifier):
    # submit() owns the same lock until its response is durably recorded.
    for attempt in range(20):
        try:
            with locked():
                plan = load_record(identifier)
                if plan["state"] != "QUEUED":
                    return
                fresh = collect()
                if plan.get("execution_phase") == "boot":
                    raise SafetyError("Ripresa EFI automatica disabilitata: generare il piano distinto di inizializzazione della nuova ESP.")
                else:
                    proposed = build_plan(fresh, plan["request"])
                    if not same_plan(plan, proposed):
                        plan["state"] = "BLOCKED"
                        plan["error"] = "Verifica immediata fallita: inventario cambiato o prerequisiti non soddisfatti."
                        save_record(plan)
                        return
                plan["state"] = "RUNNING"
                save_record(plan)
                try:
                    for index in range(plan.get("next_step", 0), len(plan["steps"])):
                        step = plan["steps"][index]
                        verify_target(plan)
                        if step['phase'] == 'clean_boot':
                            checked = plan_replacement(collect(), plan['request'])
                            if checked['blocks'] or checked['fingerprint'] != plan['fingerprint']:
                                raise SafetyError('Registro o mirror cambiati: pulizia UUID annullata.')
                        if step["phase"] in {"replace", "offline"}:
                            if plan.get("source"):
                                verify_copied_layout(plan)
                            if step["phase"] == "replace" and plan["role"] in {"BOOT", "MISTO"}:
                                bind_esp(plan)
                            # Recheck remaining members immediately before changing ZFS.
                            current = collect()
                            current_pool = next((p for p in current["pools"] if p["guid"] == plan["pool_guid"]), None)
                            if not current["complete"] or not current_pool:
                                raise SafetyError("Pool o inventario non verificabile prima della sostituzione.")
                            if topology_fingerprint(current_pool) != plan["topology_fingerprint"]:
                                raise SafetyError("La topologia del pool è cambiata durante la preparazione.")
                            peers = [m for m in leaves(current_pool) if m["guid"] != plan["old_guid"]]
                            if any(m["state"] not in {"ONLINE", "HEALTHY"} or any(m["errors"]) for m in peers):
                                raise SafetyError("Un membro rimanente è cambiato prima della sostituzione ZFS.")
                        if step["phase"] in {"replace", "boot"} and "-part" in step["argv"][-1]:
                            verify_partition(plan, step["argv"][-1])
                        if step["phase"] == "boot":
                            verify_partition(plan, step["argv"][2])
                            verify_boot_write(plan, step)
                        event = {"phase": step["phase"], "pool_guid": plan["pool_guid"], "target": plan["target"]["by_id"], "step": index, "command": step["command"], "started_at": utcnow(), "state": "STARTED"}
                        plan["events"].append(event)
                        save_record(plan)
                        result = run(step["argv"], timeout=120)
                        event.update({"finished_at": utcnow(), "state": "OK" if result["code"] == 0 else "FAILED", **result})
                        save_record(plan)
                        if result["code"]:
                            raise SafetyError("Comando fallito. Non ripetere automaticamente: " + step["command"])
                        plan["next_step"] = index + 1
                        if step["phase"] == "replace" and plan["role"] in {"BOOT", "MISTO"}:
                            plan["state"] = "WAITING_RESILVER"
                            plan["boot_test"] = "NON_TESTATO"
                            save_record(plan)
                            return
                        save_record(plan)
                    if plan.get("action") == "offline":
                        check = collect()
                        p = next((p for p in check["pools"] if p["guid"] == plan["pool_guid"]), None)
                        m = next((m for m in leaves(p) if m["guid"] == plan["old_guid"]), None) if p else None
                        if not m or m["state"] not in {"OFFLINE", "UNAVAIL", "REMOVED"}:
                            raise SafetyError("Stato dopo offline non confermato: verificare prima della rimozione.")
                        plan["state"] = "READY_FOR_REMOVAL"
                    else:
                        plan["state"] = "MONITORING"
                    plan["boot_test"] = "NON_TESTATO" if plan.get("action") != "offline" and plan["role"] in {"BOOT", "MISTO"} else "NON_APPLICABILE"
                except Exception as exc:
                    plan["state"] = "UNKNOWN" if plan["events"] and plan["events"][-1]["state"] == "STARTED" else "FAILED"
                    plan["error"] = str(exc)
                save_record(plan)
                return
        except SafetyError as exc:
            if "altro intervento" not in str(exc) or attempt == 19:
                plan = load_record(identifier)
                plan["state"], plan["error"] = "BLOCKED", str(exc)
                save_record(plan)
                return
            time.sleep(0.25)


def get_status(identifier):
    record_path(identifier)
    with locked("status-" + identifier):
        return _get_status(identifier)


def _get_status(identifier):
    plan = load_record(identifier)
    is_boot = plan.get("role") in {"BOOT", "MISTO"} and plan.get("action") != "offline"
    review_states = {"MONITORING", "WAITING_RESILVER", "BOOT_REVIEW_REQUIRED", "BOOT_TEST_REQUIRED", "BOOT_TEST_PENDING", "ESP_INITIALIZATION_REQUIRED", "ZFS_REVIEW_REQUIRED"}
    if plan["state"] in review_states or (is_boot and plan["state"] == "COMPLETED"):
        snapshot = collect()
        pool = next((p for p in snapshot["pools"] if p["guid"] == plan["pool_guid"]), None)
        plan["scan"] = pool.get("scan", "") if pool else "Pool non rilevato"
        plan["scan_percent"] = pool.get("scan_percent") if pool else None
        rate = re.search(r'(\S+/s)', plan['scan'])
        eta = re.search(r'([^,\n]+) to go', plan['scan'])
        plan['scan_rate'] = rate[1] if rate else 'Non disponibile'
        plan['scan_eta'] = eta[1].strip() if eta else 'Non disponibile'
        plan["last_checked"] = snapshot["collected_at"]
        ready = bool(pool and zfs_ready(plan, snapshot, pool))
        plan["zfs_state"] = "OK" if ready else "IN_CORSO" if "in progress" in plan["scan"] else "DA_VERIFICARE"
        plan["resilver_state"] = "OK" if ready else plan["zfs_state"]
        if is_boot and pool:
            plan["boot_validation"] = boot_report(snapshot, pool)
        if ready and plan["state"] in {"WAITING_RESILVER", "ESP_INITIALIZATION_REQUIRED"}:
            with locked():
                persisted = load_record(identifier)
                if persisted["state"] != plan["state"]:
                    return persisted
                try:
                    boot_resume_checks(plan, snapshot)
                    plan["state"] = "ESP_INITIALIZATION_REQUIRED"
                    plan["result_message"] = "Resilver concluso. In Avvio EFI selezionare il nuovo disco e Inizializza nuova ESP dopo sostituzione disco. È richiesta una nuova approvazione prima di formattare."
                except Exception as exc:
                    plan["state"], plan["error"] = "BOOT_REVIEW_REQUIRED", str(exc)
                save_record(plan)
            return plan
        if ready:
            plan["after"] = snapshot
            if is_boot:
                report = plan["boot_validation"]
                expected = {plan["target"]["serial"], (plan.get("source") or {}).get("serial", "")}
                proofs = plan.get("autonomous_tests", {})
                tested = ("" not in expected and len(expected) == 2 and all(proofs.get(s, {}).get("state") == "VERIFIED_BY_OPERATOR" for s in expected)
                          and len({proofs[s].get("boot_id") for s in expected if s in proofs}) == 2
                          and all(any(r["serial"] == s and r.get("evidence_digest") == proofs[s].get("evidence_digest") for r in report["disks"]) for s in expected))
                config_ready = report["configuration_ready"] and not report["warnings"]
                plan["registry_cleanup"] = {"state": "REQUIRED" if snapshot.get("boot", {}).get("missing_uuids") else "OK",
                                            "missing_uuids": snapshot.get("boot", {}).get("missing_uuids", [])}
                if plan["registry_cleanup"]["state"] == "REQUIRED":
                    config_ready = False
                if plan.get("action") == "initialize_esp":
                    target_row = next((r for r in report["disks"] if r["by_id"] == plan["target"]["by_id"]), {})
                    current_esp = target_row.get("esp") or {}
                    config_ready = bool(config_ready and plan.get("formatted_uuid")
                        and current_esp.get("uuid") == plan["formatted_uuid"]
                        and current_esp.get("partuuid", "").lower() == plan["expected_esp"]["partuuid"].lower())
                plan["state"] = "COMPLETED" if config_ready and tested else "BOOT_TEST_PENDING" if config_ready else "BOOT_REVIEW_REQUIRED"
                plan["boot_test"] = "VERIFICATO_DALL_OPERATORE" if tested else "NON_TESTATO_COMPLETAMENTE"
                plan["result_message"] = ("ZFS e configurazione EFI verificati; prove di avvio autonomo registrate dall'operatore." if config_ready and tested else
                    "Mirror ZFS ricostruito; configurazione EFI verificata, avvio autonomo NON TESTATO su entrambi i dischi." if config_ready else
                    "Mirror ZFS ricostruito; configurazione boot da verificare. Non è un successo completo.")
            else:
                plan["state"] = "COMPLETED"
            if plan["state"] == "COMPLETED":
                plan["completed_at"] = utcnow()
        elif plan["state"] != "WAITING_RESILVER":
            plan["state"] = "MONITORING" if "in progress" in plan["scan"] else "ZFS_REVIEW_REQUIRED"
        save_record(plan)
    elif plan["state"] in {"RUNNING", "QUEUED"}:
        # A STARTED command after an unexpected reboot must never be replayed.
        import fcntl
        try:
            with (state_dir() / "operation.lock").open("a+") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if time.time() - plan["created_at"] > 60:
                    plan["state"] = "UNKNOWN"
                    plan["error"] = "Worker non attivo: verificare lo stato reale. Nessun riavvio automatico dei comandi."
                    save_record(plan)
        except BlockingIOError:
            pass
    return plan


def record_boot_test(request):
    record_path(request.get("id"))
    with locked("status-" + request["id"]):
        plan = _record_boot_test(request)
    return get_status(plan["id"])


def _record_boot_test(request):
    with locked():
        plan = load_record(request.get("id"))
        if plan.get("role") not in {"BOOT", "MISTO"} or plan.get("action") == "offline" or plan["state"] in {"QUEUED", "RUNNING", "WAITING_RESILVER", "UNKNOWN", "FAILED"}:
            raise SafetyError("Non è possibile registrare una prova in questa fase.")
        if request.get("confirmed") is not True:
            raise SafetyError("Conferma della prova fisica di avvio mancante.")
        snapshot = collect()
        if snapshot["host_id"] != plan["host_id"]:
            raise SafetyError("Host diverso dal piano.")
        pool = next((p for p in snapshot["pools"] if p["guid"] == plan["pool_guid"]), None)
        if not pool or not snapshot["complete"]:
            raise SafetyError("Pool o inventario non verificabile per la prova.")
        present = [disk_for_member(snapshot, m) for m in leaves(pool)]
        present = [d for d in present if d]
        serial = request.get("serial")
        expected = {plan["target"]["serial"], (plan.get("source") or {}).get("serial", "")}
        if len(present) != 1 or present[0]["serial"] != serial or serial not in expected:
            raise SafetyError("La prova richiede un solo disco del mirror fisicamente rilevato, con il seriale dichiarato.")
        if (not snapshot.get("boot_id") or snapshot["boot_id"] == plan.get("boot_id_before")
                or snapshot.get("boot_started_at", 0) <= plan["created_at"]):
            raise SafetyError("Non risulta un nuovo avvio successivo al piano.")
        report = boot_report(snapshot, pool)
        if not any(r["serial"] == serial and r["configuration_ready"] for r in report["disks"]):
            raise SafetyError("Configurazione EFI del solo disco presente non verificata.")
        row = next(r for r in report["disks"] if r["serial"] == serial)
        nvram = snapshot.get("boot", {}).get("nvram", {})
        if nvram.get("code") != 0 or not nvram.get("current") or nvram["current"] not in row["nvram_entry_ids"]:
            raise SafetyError("BootCurrent non conferma l'avvio dalla ESP del disco dichiarato; nessuna certificazione automatica della fonte di avvio.")
        proofs = plan.setdefault("autonomous_tests", {})
        if any(s != serial and p["boot_id"] == snapshot["boot_id"] for s, p in proofs.items()):
            raise SafetyError("Non si possono certificare due dischi nello stesso avvio.")
        proofs[serial] = {"state": "VERIFIED_BY_OPERATOR", "boot_id": snapshot["boot_id"], "at": utcnow(), "evidence_digest": row["evidence_digest"]}
        save_record(plan)
    return plan


def identify(request):
    snap = collect()
    disk = next((d for d in snap["disks"] if d.get("by_id") == request.get("target")), None)
    if not disk or not disk.get("identity_verified") or not disk.get("locate_paths"):
        raise SafetyError("LED locate non disponibile tramite enclosure verificato. Usare seriale ed etichetta.")
    duration = int(request.get("seconds", 30))
    if not 5 <= duration <= 60:
        raise SafetyError("Durata LED consentita: da 5 a 60 secondi.")
    identifier = uuid.uuid4().hex
    record = {"id": identifier, "state": "LED", "target": disk["by_id"], "serial": disk["serial"],
              "created_at": time.time(), "duration": duration, "restore": {}}
    for path in disk["locate_paths"]:
        previous = read_text(path)
        if previous not in {"0", "1"}:
            raise SafetyError("Stato LED non interpretabile; nessuna modifica.")
        record["restore"][path] = previous
    with locked("led"):
        for path in state_dir().glob("*.json"):
            active = json.loads(path.read_text())
            if active.get("state") == "LED" and set(active.get("restore", {})) & set(record["restore"]):
                raise SafetyError("Identificazione LED già attiva sul vano; attendere il ripristino.")
        save_record(record)
        # Detached worker activates AND restores. No dependency on SSH.
        spawn_worker(identifier, "--led")
    return {"id": identifier, "message": f"Identificazione richiesta per {duration} secondi; verificare il vano fisicamente."}


def led_worker(identifier):
    entry = load_record(identifier)
    try:
        for path in entry["restore"]:
            Path(path).write_text("1")
        time.sleep(entry["duration"])
    finally:
        for path, previous in entry["restore"].items():
            try:
                Path(path).write_text(previous)
            except OSError as exc:
                entry["error"] = str(exc)
        entry["state"] = "LED_FINISHED"
        save_record(entry)


def shutdown(request):
    if request.get("confirmed") is not True:
        raise SafetyError("Conferma dello spegnimento mancante.")
    snapshot = collect()
    if request.get("host_id") != snapshot["host_id"]:
        raise SafetyError("Identità host diversa dall'approvazione.")
    if glob.glob("/etc/pve/ha/resources.cfg") and read_text("/etc/pve/ha/resources.cfg"):
        raise SafetyError("Risorse HA configurate: usare il piano di manutenzione del cluster.")
    if (Path("/etc/pve/corosync.conf").exists() or Path("/etc/corosync/corosync.conf").exists()
            or run(["pvecm", "status"])["code"] == 0):
        raise SafetyError("Host in cluster: spegnimento automatico non abilitato; usare manutenzione del cluster.")
    active = run(["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"])
    rows = json_output(active, [])
    if active["code"] or not isinstance(rows, list):
        raise SafetyError("Stato VM/CT non verificabile: arrestare i carichi e usare la GUI Proxmox.")
    if any(g.get("node") == snapshot["hostname"] and g.get("status") == "running" for g in rows):
        raise SafetyError("VM o container ancora accesi: arrestarli da Proxmox prima di spegnere il nodo.")
    with locked():
        if any(json.loads(p.read_text()).get("state") in {"QUEUED", "RUNNING", "WAITING_RESILVER", "MONITORING", "UNKNOWN"} for p in state_dir().glob("*.json")):
            raise SafetyError("Intervento in coda, attivo o con esito ignoto: spegnimento non consentito dall'app.")
        result = run(["shutdown", "-h", "+1"])
        if result["code"]:
            raise SafetyError(result["err"])
    return {"message": "Spegnimento richiesto fra un minuto. Verificare fisicamente il PC spento prima di scollegare cavi."}


def dispatch(request):
    if '--read-only' in sys.argv and request.get('op') not in {'collect', 'version', 'preview', 'list_jobs'}:
        raise SafetyError('Helper in sola lettura: operazione non consentita.')
    state_dir()
    operation = request.get("op")
    if operation == "collect":
        return collect()
    if operation == "version":
        return {"version": __version__, "schema": 2}
    if operation == 'preview':
        plan = build_plan(collect(), request['request'])
        return {**plan, 'id': 'dry-run', 'state': 'PREVIEW', 'events': [], 'read_only': True}
    if operation == "plan":
        return prepare(request["request"])
    if operation == "submit":
        return submit(request)
    if operation == "status":
        return get_status(request.get("id"))
    if operation == "list_jobs":
        records = [json.loads(p.read_text()) for p in state_dir().glob("*.json")]
        return sorted(records, key=lambda r: r.get("created_at", 0))[-50:]
    if operation == "identify":
        return identify(request)
    if operation == "shutdown":
        return shutdown(request)
    if operation == "record_boot_test":
        return record_boot_test(request)
    raise SafetyError("Operazione non consentita.")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--inspect-esp":
        try:
            if os.geteuid() != 0:
                raise SafetyError("Ispezione ESP richiede root.")
            print(json.dumps({"ok": True, "result": inspect_esp_namespace(sys.argv[2], run)}))
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            sys.exit(1)
        return
    if len(sys.argv) == 3 and sys.argv[1] in {"--run", "--led"}:
        state_dir()
        (perform_job if sys.argv[1] == "--run" else led_worker)(sys.argv[2])
        return
    try:
        payload = sys.stdin.buffer.read(1_000_001)
        if len(payload) > 1_000_000:
            raise SafetyError("Richiesta troppo grande.")
        result = dispatch(json.loads(payload))
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
