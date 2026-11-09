"""Pure domain logic shared by the desktop and the privileged node helper.

Only the node helper executes a plan. All values below are observations, never
shell fragments. An unsupported or incomplete observation blocks writes.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from copy import deepcopy

GOOD = {"ONLINE", "HEALTHY"}
BAD = {"FAULTED", "UNAVAIL", "OFFLINE", "REMOVED", "DEGRADED"}
STATES = GOOD | BAD | {"AVAIL", "INUSE", "SUSPENDED"}
SECTIONS = {"logs", "cache", "spares", "special", "dedup"}
ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BIOS = "21686148-6449-6e6f-744e-656564454649"
ZFS = "6a898cc3-1dd2-11b2-99a6-080020736631"
RESERVED = "6a945a3b-1dd2-11b2-99a6-080020736631"


class SafetyError(ValueError):
    """The observed system does not satisfy the operation's preconditions."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def command_text(args):
    return shlex.join(args)


def topology_type(name):
    if re.fullmatch(r"mirror-\d+", name):
        return "mirror"
    match = re.fullmatch(r"raidz([123])?-\d+", name)
    if match:
        return "raidz" + (match.group(1) or "1")
    if re.match(r"(?:replacing|spare)-\d+", name):
        return "transient"
    if name.startswith("draid"):
        return "draid"
    return "disk"


def parse_status(text):
    """Parse C-locale zpool status, retaining missing leaves and nested groups."""
    pools, pool, stack = [], None, []
    in_config, section, reading_scan = False, "data", False
    for original in text.splitlines():
        line = original.strip()
        if line.startswith("pool:"):
            pool = {"name": line[5:].strip(), "state": "UNKNOWN", "children": [],
                    "scan": "", "errors": "", "warnings": [], "guid": ""}
            pools.append(pool)
            stack, in_config, section, reading_scan = [], False, "data", False
        elif pool is None:
            continue
        elif line.startswith("state:"):
            pool["state"] = line[6:].strip()
        elif line.startswith("scan:"):
            pool["scan"] = line[5:].strip()
            reading_scan = True
        elif line.startswith("config:"):
            in_config = True
            reading_scan = False
        elif line.startswith("errors:"):
            pool["errors"] = line[7:].strip()
            in_config = False
            reading_scan = False
        elif reading_scan:
            if line:
                pool["scan"] += "\n" + line
        elif in_config:
            columns = line.split()
            if not columns or columns[0] == "NAME":
                continue
            if len(columns) == 1 and columns[0] in SECTIONS:
                section, stack = columns[0], []
                continue
            if len(columns) < 2 or columns[1] not in STATES:
                pool["warnings"].append("Riga di topologia non riconosciuta: " + line)
                continue
            indent = len(original.expandtabs(8)) - len(original.expandtabs(8).lstrip())
            counters = []
            for item in columns[2:5]:
                try:
                    counters.append(int(item))
                except ValueError:
                    counters.append(-1)
            node = {"name": columns[0], "state": columns[1], "kind": topology_type(columns[0]),
                    "errors": counters, "section": section, "children": [], "guid": ""}
            if " was " in line:
                node["was_path"] = line.split(" was ", 1)[1].strip()
            if columns[0] == pool["name"] and section == "data" and not pool["children"] and not stack:
                stack = [(indent, pool)]
                continue
            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1] if stack else pool
            parent["children"].append(node)
            stack.append((indent, node))
    for p in pools:
        match = re.search(r"(\d+(?:\.\d+)?)%\s+done", p["scan"])
        p["scan_percent"] = float(match[1]) if match else None
    return pools


def walk(node):
    yield node
    for child in node.get("children", []):
        yield from walk(child)


def leaves(node):
    return [item for item in walk(node) if not item.get("children")]


def merge_guids(pools, guid_text, pool_guids):
    """Join two status reads only when structure and states still agree."""
    other = {p["name"]: p for p in parse_status(guid_text)}
    for pool in pools:
        pool["guid"] = str(pool_guids.get(pool["name"], ""))
        candidate = other.get(pool["name"])
        a = list(walk(pool))
        b = list(walk(candidate)) if candidate else []
        if len(a) != len(b) or any(
            x["state"] != y["state"] or len(x["children"]) != len(y["children"])
            or x.get("section", "data") != y.get("section", "data") for x, y in zip(a, b)
        ):
            pool["warnings"].append("Topologia cambiata tra lettura dei percorsi e dei GUID. Aggiornare.")
            continue
        for x, y in zip(a[1:], b[1:]):
            if y["name"].isdigit():
                x["guid"] = y["name"]
            elif x["kind"] != y["kind"]:
                pool["warnings"].append("Associazione GUID non verificata.")


def data_groups(pool):
    return [x for x in pool["children"] if x.get("section") == "data"]


def raid_label(pool):
    groups = data_groups(pool)
    if not groups:
        return "SCONOSCIUTO"
    labels = []
    for group in groups:
        kind = ("unknown" if group["kind"] == "disk" and group["children"] else group["kind"]).upper()
        labels.append(f"{kind} · {len(leaves(group))} membri")
    return " + ".join(labels)


def storage_config(text):
    result, current = [], None
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            match = re.match(r"(\S+):\s+(\S+)", line)
            if match:
                current = {"type": match[1], "id": match[2]}
                result.append(current)
        elif current and line.strip() and not line.lstrip().startswith("#"):
            parts = line.split(None, 1)
            if len(parts) == 2:
                current[parts[0]] = parts[1].strip()
    return result


def disk_for(snapshot, path):
    if not path:
        return None
    for disk in snapshot.get("disks", []):
        if path in [disk["path"], disk.get("by_id", ""), *disk.get("aliases", [])]:
            return disk
        for partition in disk.get("partitions", []):
            if path in [partition["path"], *partition.get("aliases", [])]:
                return disk
    return None


def partition_for(disk, path):
    return next((p for p in disk.get("partitions", [])
                 if path in [p["path"], *p.get("aliases", [])]), None)


def disk_for_member(snapshot, member):
    disk = disk_for(snapshot, member["name"])
    if disk and member["state"] not in GOOD and disk.get("signatures_checked"):
        zfs_labels = (any(s.get("type") == "zfs_member" for s in disk.get("signatures", []))
                      or any(p.get("fstype") == "zfs_member" for p in disk.get("partitions", [])))
        # /dev/sdX can be reused by a blank replacement. A stale UNAVAIL/OFFLINE
        # pathname must not claim that new disk as the old physical member.
        if not zfs_labels:
            return None
    return disk


def classify(snapshot):
    root = snapshot.get("root", {})
    mounts = snapshot.get("mounts", [])
    boot_pools = set()
    for mount in [root, *mounts]:
        if mount.get("target") in {"/", "/boot", "/boot/efi"} and mount.get("fstype") == "zfs":
            boot_pools.add(mount.get("source", "").split("/")[0])
    if root.get("fstype") == "zfs":
        boot_pools.add(root.get("source", "").split("/")[0])
    for pool in snapshot.get("pools", []):
        stores = [s["id"] for s in snapshot.get("storages", [])
                  if s.get("type") == "zfspool" and s.get("pool", "").split("/")[0] == pool["name"]
                  and (not s.get("nodes") or snapshot.get("hostname") in s["nodes"].split(","))]
        physical_boot = False
        for member in leaves(pool):
            disk = disk_for(snapshot, member["name"])
            if disk and (any(p.get("parttype", "").lower() in {ESP, BIOS} for p in disk.get("partitions", []))
                         or any(disk_for(snapshot, m.get("source", "")) == disk for m in mounts
                                if m.get("target") in {"/", "/boot", "/boot/efi"})):
                physical_boot = True
        boot = pool["name"] in boot_pools or physical_boot
        pool["role"] = "MISTO" if boot and stores else "BOOT" if boot else "DATI_VM" if stores else "ALTRO"
        if not root.get("fstype"):
            pool["role"] = "SCONOSCIUTO"
        pool["storages"] = stores
        pool["guests"] = [g for g in snapshot.get("guests", []) if set(g["storages"]) & set(stores)]
    return snapshot


def safety_fingerprint(snapshot):
    def canonical(value):
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, list):
            items = [canonical(v) for v in value]
            return sorted(items, key=digest) if all(isinstance(v, dict) for v in items) else items
        return value
    return digest(canonical({"host": snapshot.get("host_id"), "root": snapshot.get("root"),
                   "boot_id": snapshot.get("boot_id"),
                   "pools": snapshot.get("pools"),
                   "disks": [{k: v for k, v in d.items() if k not in {"smart", "temperature"}}
                             for d in snapshot.get("disks", [])],
                   "boot": snapshot.get("boot"), "version": snapshot.get("pve_version")}))


def topology_fingerprint(pool):
    def shape(node):
        return {k: node.get(k) for k in ("name", "guid", "kind", "section", "state")} | {
            "children": [shape(child) for child in node.get("children", [])]}
    return digest(shape(pool))


def plan_replacement(snapshot, request):
    """Return an executable, server-derived plan or explicit blocking reasons."""
    if request.get("action") == "offline":
        return plan_offline(snapshot, request)
    if request.get('action') == 'clean_boot':
        plan = plan_boot_repair(snapshot, request)
        from .boot import boot_report
        pool = next(p for p in snapshot['pools'] if p['guid'] == plan['pool_guid'])
        if not boot_report(snapshot, pool)['configuration_ready'] or pool['state'] != 'ONLINE':
            plan['blocks'].append('Pulizia registro consentita solo dopo ricostruzione e verifica EFI di entrambi i membri.')
        missing = snapshot.get('boot', {}).get('missing_uuids', [])
        if not missing:
            plan['blocks'].append('Nessun UUID assente verificato.')
        plan.update(action='clean_boot', missing_uuids=missing, steps=[
            {'argv': ['proxmox-boot-tool', 'clean'], 'command': 'proxmox-boot-tool clean', 'phase': 'clean_boot', 'explanation': 'Rimuove dal registro i riferimenti alle ESP assenti: ' + ', '.join(missing) + '. Confermare che i dischi non siano temporaneamente scollegati.'},
            {'argv': ['proxmox-boot-tool', 'status'], 'command': 'proxmox-boot-tool status', 'phase': 'verify', 'explanation': 'Verifica il registro dopo la pulizia approvata.'}])
        return plan
    if request.get("action") == "initialize_esp":
        return plan_boot_repair(snapshot, request, initialize=True)
    if request.get("action") == "repair_boot":
        return plan_boot_repair(snapshot, request)
    blocks, warnings, steps = [], [], []
    pool = next((p for p in snapshot["pools"] if p["guid"] == str(request.get("pool_guid"))), None)
    if pool is None:
        raise SafetyError("Pool non trovato tramite GUID. Aggiornare l'inventario.")
    old = next((n for n in leaves(pool) if n["guid"] == str(request.get("old_guid"))), None)
    target = next((d for d in snapshot["disks"] if d.get("by_id") == request.get("target")), None)
    if old is None or target is None:
        raise SafetyError("Membro da sostituire o disco nuovo non identificato.")
    profile = request.get("profile", "laboratorio")
    if profile not in {"laboratorio", "produzione"}:
        raise SafetyError("Profilo hardware sconosciuto.")
    if snapshot.get("demo"):
        blocks.append("Modalità dimostrativa: nessun comando viene eseguito su server.")
    if not re.match(r"^pve-manager/(8|9)\.", snapshot.get("pve_version", "")):
        blocks.append("Versione Proxmox diversa dalle famiglie 8 e 9 riconosciute.")
    if not snapshot.get("complete") or snapshot.get("issues"):
        blocks.append("Inventario incompleto: risolvere gli errori di raccolta prima di procedere.")
    if not snapshot.get("host_id") or not pool["guid"].isdigit() or not old["guid"].isdigit():
        blocks.append("Identità di host, pool o membro incompleta.")
    if pool.get("warnings"):
        blocks.extend(pool["warnings"])
    if pool["state"] not in GOOD | {"DEGRADED"}:
        blocks.append("Pool non operativo: non è una sostituzione ordinaria.")
    if pool.get("errors") != "No known data errors":
        blocks.append("Errori permanenti o stato degli errori non verificato.")
    if re.search(r"in progress|resilver|rebuild", pool.get("scan", ""), re.I) and "in progress" in pool.get("scan", ""):
        blocks.append("Ricostruzione o scrub già in corso.")
    groups = data_groups(pool)
    group = next((g for g in groups if old in leaves(g)), None)
    member_ids = [m.get('guid') for m in leaves(pool)]
    if not all(member_ids) or len(set(member_ids)) != len(member_ids):
        blocks.append('GUID dei membri mancanti o duplicati: topologia ambigua.')
    if group is None or any(x.get("section") != "data" for x in pool["children"]):
        blocks.append("Classi ausiliarie, spare o vdev non identificato: esecuzione non supportata.")
    kind = group["kind"] if group else "unknown"
    members = leaves(group) if group else []
    if not ((kind == "mirror" and len(members) >= 2) or (kind in {"raidz1", "raidz2", "raidz3"} and len(members) >= int(kind[-1]) + 1)):
        blocks.append("Vdev non supportato: richiesti mirror o RAIDZ1/2/3 con membri sufficienti.")
    for other in groups:
        if other is not group and (other['kind'] not in {'mirror', 'raidz1', 'raidz2', 'raidz3'} or any(m['state'] not in GOOD or any(m['errors']) or not disk_for_member(snapshot, m) for m in leaves(other))):
            blocks.append('Altro vdev non supportato, degradato o non verificabile.')
    if any(n.get("kind") == "transient" for n in walk(pool)):
        blocks.append("Sostituzione o spare già attivo nella topologia.")
    if old["state"] not in BAD:
        blocks.append("Il membro selezionato non risulta guasto o escluso; nessuna sostituzione preventiva automatica.")
    survivors = [m for m in members if m is not old]
    for member in survivors:
        if member["state"] not in GOOD or len(member["errors"]) != 3 or any(member["errors"]):
            blocks.append("Un membro rimanente ha errori o stato non sano: " + member["name"])
        if not disk_for_member(snapshot, member):
            blocks.append("Identità fisica di un membro rimanente non risolta.")
    used = [disk_for_member(snapshot, n) for p in snapshot["pools"] for n in leaves(p)]
    if target in used:
        blocks.append("Il disco di destinazione appartiene già a un pool.")
    serial = target.get("serial", "").strip()
    if not serial or sum(d.get("serial", "").strip() == serial for d in snapshot["disks"]) != 1:
        blocks.append("Seriale della destinazione assente o non univoco.")
    if not target.get("by_id", "").startswith("/dev/disk/by-id/") or not target.get("identity_verified"):
        blocks.append("Percorso persistente della destinazione non verificato.")
    if target.get("partitions") or target.get("signatures") or target.get("mounts") or target.get("holders") or target.get("ro"):
        blocks.append("Il nuovo disco non è vuoto e libero oppure è in sola lettura.")
    if not target.get("signatures_checked"):
        blocks.append("Controllo delle firme sul disco nuovo non riuscito.")
    reference_disks = [disk_for_member(snapshot, n) for n in members]
    reference_disks = [d for d in reference_disks if d]
    required = max((d["size"] for d in reference_disks), default=0)
    if not required or target.get("size", 0) < required:
        blocks.append("Capacità del nuovo disco inferiore al riferimento verificabile.")
    if any(d.get("log_sec") != target.get("log_sec") for d in reference_disks):
        blocks.append("Dimensione dei settori logici differente dai membri esistenti.")
    if pool.get("role") not in {"BOOT", "MISTO", "DATI_VM"}:
        blocks.append("Ruolo del pool non verificato come boot o storage VM.")
    old_disk = disk_for_member(snapshot, old)
    if old_disk and old_disk is target:
        blocks.append("Il nuovo disco coincide con il disco da sostituire.")
    if profile == "laboratorio" and old_disk is not None:
        blocks.append("Laboratorio senza hot swap: spegnere, sostituire e riaccendere prima di pianificare il nuovo disco.")
    if profile == "produzione" and not request.get("hot_swap_confirmed"):
        blocks.append("Confermare il supporto hot swap del modello e dell'alloggiamento.")
    for member in survivors:
        d = disk_for(snapshot, member["name"])
        smart = d.get("smart", {}) if d else {}
        if smart.get("passed") is False or (smart.get("exit_code", 0) & 0b11111000):
            blocks.append("Allarme SMART sul membro rimanente: " + member["name"])
    warnings.append("La capacità richiesta è conservativa: almeno il disco più grande del vdev osservato.")
    warnings.append("SMART e contatori puliti non garantiscono l'assenza di guasti latenti; verificare il backup.")

    def add(args, explanation, phase):
        steps.append({"argv": args, "command": command_text(args), "explanation": explanation, "phase": phase})

    boot = pool.get("role") in {"BOOT", "MISTO"}
    new_member = target["by_id"]
    source = None
    if boot:
        from .boot import boot_report
        report = boot_report(snapshot, pool)
        if snapshot.get("boot", {}).get("firmware_mode") != "UEFI":
            blocks.append("Modalità firmware non verificata come UEFI; BIOS Legacy richiede una procedura distinta.")
        if snapshot.get("boot", {}).get("architecture") not in {"x86_64", "amd64"}:
            blocks.append("Architettura boot x86_64 non verificata.")
        if len(groups) != 1 or len(members) != 2 or kind != "mirror" or snapshot.get("root", {}).get("fstype") != "zfs" or snapshot["root"].get("source", "").split("/")[0] != pool["name"]:
            blocks.append("Boot supportato solo su root ZFS in mirror di due membri.")
        source = disk_for(snapshot, survivors[0]["name"]) if survivors else None
        source_report = next((r for r in report["disks"] if source and r["by_id"] == source.get("by_id")), None)
        if not source_report or not source_report["configuration_ready"]:
            blocks.append("Il membro di riferimento non ha una configurazione EFI verificata. Diagnosticare e ripristinare separatamente la sua ESP prima della sostituzione.")
        part = partition_for(source, survivors[0]["name"]) if source else None
        esps = [p for p in source.get("partitions", []) if p.get("parttype", "").lower() == ESP] if source else []
        table = source.get("table", {}) if source else {}
        if not source or not source.get("by_id") or not part or not part.get("number") or len(esps) != 1 or table.get("label") != "gpt":
            blocks.append("Layout GPT, partizione ZFS o ESP di riferimento non verificato.")
        elif (part is esps[0] or part.get("parttype", "").lower() != ZFS
              or esps[0].get("fstype") != "vfat" or not esps[0].get("number")
              or part["number"] == esps[0]["number"]
              or len({p.get("number") for p in source["partitions"]}) != len(source["partitions"])
              or len(source["partitions"]) not in {2, 3} or
              any(p is not part and p is not esps[0] and p.get("parttype", "").lower() != BIOS for p in source["partitions"])):
            blocks.append("Layout di boot personalizzato non supportato.")
        else:
            esp = esps[0]
            matching = [line.lower() for line in snapshot.get("boot", {}).get("status", "").splitlines()
                        if esp.get("uuid") and esp["uuid"].lower() in line.lower() and "configured with:" in line.lower()]
            modes = {"grub" if "configured with: grub" in line else "systemd" if "configured with: uefi" in line else "unknown" for line in matching}
            if len(modes) != 1 or "unknown" in modes:
                blocks.append("Modalità di proxmox-boot-tool non verificata sull'ESP sana; GRUB legacy richiede istruzioni dedicate.")
            else:
                add(["sgdisk", source["by_id"], "-R", target["by_id"]], "Copia la GPT dal membro sano al SOLO nuovo disco.", "partition")
                add(["sgdisk", "-G", target["by_id"]], "Rigenera i GUID sul nuovo disco.", "partition")
                add(["partprobe", target["by_id"]], "Rilegge le partizioni sul nuovo disco.", "settle")
                add(["udevadm", "settle", "--timeout=30"], "Attende i nuovi percorsi persistenti.", "settle")
                new_member = target["by_id"] + "-part" + str(part["number"])
    elif any(partition_for(disk_for(snapshot, m["name"]) or {}, m["name"]) for m in survivors):
        source = disk_for(snapshot, survivors[0]["name"])
        part = partition_for(source, survivors[0]["name"]) if source else None
        supported = bool(source and source.get("by_id") and source.get("table", {}).get("label") == "gpt"
                         and part and part.get("number") and part.get("parttype", "").lower() == ZFS
                         and len(source.get("partitions", [])) in {1, 2}
                         and all(p.get("parttype", "").lower() in {ZFS, RESERVED} for p in source["partitions"])
                         and sum(p.get("parttype", "").lower() == ZFS for p in source["partitions"]) == 1)
        if not supported or any(disk_for(snapshot, m["name"]) is None or
                               partition_for(disk_for(snapshot, m["name"]), m["name"]) is None for m in survivors):
            blocks.append("Pool VM su partizioni: layout diverso da GPT ZFS con eventuale partizione riservata.")
        else:
            add(["sgdisk", source["by_id"], "-R", target["by_id"]], "Replica il layout GPT dati sul SOLO nuovo disco, senza copiare filesystem.", "partition")
            add(["sgdisk", "-G", target["by_id"]], "Rigenera i GUID del nuovo disco dati.", "partition")
            add(["partprobe", target["by_id"]], "Rilegge le partizioni dati sul ricambio.", "settle")
            add(["udevadm", "settle", "--timeout=30"], "Attende i percorsi del nuovo disco.", "settle")
            new_member = target["by_id"] + "-part" + str(part["number"])
    add(["zpool", "replace", pool["name"], old["guid"], new_member],
        "Sostituisce il membro nel vdev esistente e avvia il resilver, senza forzature.", "replace")
    if boot and source and steps and steps[0]["phase"] == "partition":
        esp_path = target["by_id"] + "-part" + str(esps[0]["number"])
        warnings.append("Dopo il resilver occorre approvare un piano distinto per inizializzare la nuova ESP; nessuna formattazione EFI automatica.")
    else:
        esp_path = None
    needed = {step["argv"][0] for step in steps}
    missing = needed - set(snapshot.get("tools", []))
    if missing:
        blocks.append("Strumenti mancanti: " + ", ".join(sorted(missing)))
    return {"action": "replace", "request": deepcopy(request), "host_id": snapshot.get("host_id"),
            "pool": pool["name"], "pool_guid": pool["guid"], "role": pool.get("role"),
            "raid": raid_label(pool), "old_guid": old["guid"], "old_name": old["name"],
            "target": deepcopy(target), "source": deepcopy(source), "new_member": new_member,
            "planned_esp_path": esp_path,
            "fingerprint": safety_fingerprint(snapshot), "topology_fingerprint": topology_fingerprint(pool), "steps": steps,
            "boot_id_before": snapshot.get("boot_id", ""),
            "boot_before": report if boot else None,
            "blocks": list(dict.fromkeys(blocks)), "warnings": warnings,
            "guests": pool.get("guests", [])}


def plan_boot_repair(snapshot, request, initialize=False):
    """Repair only the selected ESP; never recreate a healthy ZFS mirror."""
    from .boot import boot_report
    blocks, steps = [], []
    pool = next((p for p in snapshot["pools"] if p["guid"] == str(request.get("pool_guid"))), None)
    if not pool:
        raise SafetyError("Pool di boot non identificato.")
    target = next((d for d in snapshot["disks"] if d.get("by_id") == request.get("target")), None)
    member = next((m for m in leaves(pool) if disk_for_member(snapshot, m) == target), None) if target else None
    if not target or not member:
        raise SafetyError("Disco selezionato non associato a un membro del pool di boot.")
    report = boot_report(snapshot, pool)
    row = next((r for r in report["disks"] if r["by_id"] == target["by_id"]), {})
    peers = [r for r in report["disks"] if r["by_id"] != target["by_id"]]
    source_row = next((r for r in peers if r["configuration_ready"]), None)
    source = next((d for d in snapshot["disks"] if source_row and d.get("by_id") == source_row["by_id"]), None)
    survivor_repair = (pool['state'] == 'DEGRADED' and member['state'] in GOOD
        and all(disk_for_member(snapshot, m) is None and m['state'] in BAD for m in leaves(pool) if m is not member)
        and row.get('mode') in {'grub', 'systemd'}
        and all(row.get('checks', {}).get(k, {}).get('state') == 'OK' for k in ('fat32', 'filesystem', 'identity', 'registration', 'configuration')))
    if initialize:
        survivor_repair = False
    if survivor_repair:
        source_row, source = row, target
    groups = data_groups(pool)
    if not (len(groups) == 1 and groups[0]["kind"] == "mirror" and len(leaves(groups[0])) == 2
            and len(pool["children"]) == 1 and pool.get("role") in {"BOOT", "MISTO"}
            and snapshot.get("root", {}).get("fstype") == "zfs"
            and snapshot["root"].get("source", "").split("/")[0] == pool["name"]):
        blocks.append("Ripristino EFI supportato solo sul mirror root ZFS di due membri senza vdev ausiliari.")
    if not snapshot.get("complete") or snapshot.get("issues") or pool.get("warnings"):
        blocks.append("Inventario o topologia incompleti.")
    if not re.match(r"^pve-manager/(8|9)\.", snapshot.get("pve_version", "")):
        blocks.append("Versione Proxmox non supportata.")
    if snapshot.get("demo"):
        blocks.append("Modalità dimostrativa: nessuna modifica reale.")
    if snapshot.get("boot", {}).get("firmware_mode") != "UEFI" or snapshot.get("boot", {}).get("architecture") not in {"x86_64", "amd64"}:
        blocks.append("Ripristino EFI disponibile solo con UEFI x86_64 verificato, non BIOS Legacy.")
    if (pool["state"] not in GOOD and not survivor_repair or pool["errors"] != "No known data errors" or "in progress" in pool["scan"]
            or any((m['state'] not in GOOD and not (survivor_repair and m is not member)) or len(m["errors"]) != 3 or any(m["errors"]) for m in leaves(pool))):
        blocks.append("Prima del ripristino EFI il mirror ZFS deve essere ONLINE, senza errori o resilver attivo.")
    if not source or not source_row:
        blocks.append("Nessun altro membro con configurazione EFI verificata: richiede diagnosi manuale.")
    if (not target.get("identity_verified") or not target.get("serial") or target.get("ro")
            or sum(d.get("serial") == target.get("serial") for d in snapshot["disks"]) != 1):
        blocks.append("Identità o accessibilità del disco selezionato non verificata.")
    esp = row.get("esp")
    if not esp or target.get("table", {}).get("label") != "gpt" or not esp.get("partuuid"):
        blocks.append("ESP GPT del disco selezionato non identificata in modo univoco.")
    mode = source_row["mode"] if source_row else "unknown"
    if row.get("mode") not in {"unknown", mode}:
        blocks.append("Bootloader diversi fra i membri: non viene eseguita una migrazione implicita.")
    if snapshot.get("boot", {}).get("secure_boot") is True and mode != "grub":
        blocks.append("Secure Boot attivo: systemd-boot non viene inizializzato automaticamente.")
    path = target["by_id"] + "-part" + str(esp["number"]) if esp else ""
    info = snapshot.get("boot", {}).get("esps", {}).get(esp["path"], {}) if esp else {}
    if esp and (esp.get("mounts") or any(m.get("source") in [esp["path"], *esp.get("aliases", [])] for m in snapshot.get("mounts", []))):
        blocks.append("ESP selezionata montata: nessuno smontaggio o ripristino automatico.")
    if not initialize and (info.get("probe", {}).get("TYPE") != "vfat"
                             or info.get("probe", {}).get("VERSION", "").upper() != "FAT32" or info.get("fsck_code") != 0):
        blocks.append("Il ripristino richiede una ESP FAT32 coerente. Per una nuova ESP usare 'Inizializza nuova ESP dopo sostituzione disco'; nessuna formattazione implicita.")
    evidence = snapshot.get("esp_initialization", {}) if initialize else {}
    if initialize:
        if mode not in {"grub", "systemd"}:
            blocks.append("Bootloader del membro di riferimento non verificato; nessuna migrazione implicita.")
        if not evidence.get("replacement_id"):
            blocks.append("Sostituzione precedente e PARTUUID non verificati nel registro del nodo.")
        if evidence.get("error") or not evidence.get("blank", {}).get("all_zero"):
            blocks.append(evidence.get("error") or "Assenza di dati non dimostrata: occorrono verifica delle firme e lettura integrale della ESP.")
        if not target.get("wwn") or sum(d.get("wwn") == target.get("wwn") for d in snapshot["disks"]) != 1:
            blocks.append("WWN del nuovo disco assente o non univoco: identità incompleta.")
        zfs_parts = [p for p in target.get("partitions", []) if p.get("parttype", "").lower() == ZFS
                     and member["name"] in [p["path"], *p.get("aliases", [])]]
        if (not esp or esp.get("parttype", "").lower() != ESP or len(zfs_parts) != 1
                or esp["path"] == zfs_parts[0]["path"] or target.get("holders")
                or sum(p.get("partuuid", "").lower() == esp.get("partuuid", "").lower()
                       for d in snapshot["disks"] for p in d.get("partitions", [])) != 1):
            blocks.append("ESP e partizione ZFS non distinte in modo univoco, PARTUUID duplicato o dispositivi dipendenti presenti.")

    def add(argv, message, phase="boot"):
        steps.append({"argv": argv, "command": command_text(argv), "explanation": message, "phase": phase})
    if initialize and path:
        add(["proxmox-boot-tool", "format", path], "Crea FAT32 sulla sola nuova ESP, dopo verifica integrale e conferma esplicita; non modifica GPT o ZFS.")
    if mode != "unknown" and path:
        add(["proxmox-boot-tool", "init", path] + (["grub"] if mode == "grub" else []), "Ripristina il bootloader sulla sola ESP selezionata.")
        add(["proxmox-boot-tool", "refresh"], "Sincronizza kernel e configurazioni su tutte le ESP già registrate.", "boot_refresh")
        add(["proxmox-boot-tool", "status"], "Rilegge il registro; i file e le vie UEFI saranno controllati separatamente.", "verify")
    missing = {s["argv"][0] for s in steps} - set(snapshot.get("tools", []))
    if missing or not steps:
        blocks.append("Strumenti o modalità di ripristino mancanti: " + ", ".join(sorted(missing)))
    return {"action": "initialize_esp" if initialize else "repair_boot", "request": deepcopy(request), "host_id": snapshot.get("host_id"),
        "initialization_evidence": deepcopy(evidence), "replacement_id": evidence.get("replacement_id"),
        "missing_uuids": snapshot.get("boot", {}).get("missing_uuids", []),
        "pool": pool["name"], "pool_guid": pool["guid"], "role": pool["role"], "raid": raid_label(pool),
        "old_guid": member["guid"], "old_name": member["name"], "target": deepcopy(target), "source": deepcopy(source),
        "new_member": member["name"], "expected_esp": deepcopy(esp), "boot_before": report,
        "boot_id_before": snapshot.get("boot_id", ""), "fingerprint": safety_fingerprint(snapshot),
        "topology_fingerprint": topology_fingerprint(pool), "steps": steps, "blocks": blocks,
        "warnings": ["Ripristino della sola ESP: nessun sgdisk, zpool replace o grub-install generico.",
                     "Gli UUID EFI assenti richiedono un piano separato di pulizia, dopo la verifica di entrambe le ESP.",
                     "La prova di avvio autonomo richiede una manutenzione e non è dedotta dai file EFI."],
        "guests": pool.get("guests", [])}


def plan_offline(snapshot, request):
    """Prepare a faulty, present member for hot removal; never detach it."""
    pool = next((p for p in snapshot["pools"] if p["guid"] == str(request.get("pool_guid"))), None)
    if not pool:
        raise SafetyError("Pool non identificato.")
    old = next((m for m in leaves(pool) if m["guid"] == str(request.get("old_guid"))), None)
    disk = disk_for_member(snapshot, old) if old else None
    if not old or not disk:
        raise SafetyError("Disco già assente o non identificato: offline non necessario o non verificabile.")
    blocks = []
    groups = data_groups(pool)
    if (len(groups) != 1 or groups[0]["kind"] not in {"mirror", "raidz1", "raidz2"}
            or len(leaves(groups[0])) != (2 if groups[0]["kind"] == "mirror" else 3)
            or any(n.get("kind") == "transient" for n in walk(pool))
            or any(g.get("section") != "data" for g in pool["children"])):
        blocks.append("Topologia non supportata per l'esclusione guidata.")
    if not snapshot.get("complete") or snapshot.get("issues") or pool.get("warnings"):
        blocks.append("Inventario o topologia non completamente verificati.")
    if snapshot.get("demo"):
        blocks.append("Modalità dimostrativa: nessuna modifica reale.")
    if not re.match(r"^pve-manager/(8|9)\.", snapshot.get("pve_version", "")):
        blocks.append("Versione Proxmox non supportata.")
    if pool["state"] not in GOOD | {"DEGRADED"} or pool["errors"] != "No known data errors" or "in progress" in pool["scan"]:
        blocks.append("Stato pool, errori permanenti o ricostruzione incompatibile.")
    if pool.get("role") not in {"BOOT", "MISTO", "DATI_VM"}:
        blocks.append("Ruolo boot o VM non verificato.")
    if old["state"] not in {"FAULTED", "DEGRADED"}:
        blocks.append("Esclusione disponibile solo per un membro presente già dichiarato problematico.")
    for member in leaves(pool):
        if member is old:
            continue
        other = disk_for(snapshot, member["name"])
        if not other or member["state"] not in GOOD or len(member["errors"]) != 3 or any(member["errors"]):
            blocks.append("Un altro membro non è sano o non è identificato.")
        elif other.get("smart", {}).get("passed") is False or other.get("smart", {}).get("exit_code", 0) & 248:
            blocks.append("Allarme SMART su un altro membro.")
    if not snapshot.get("host_id") or not pool["guid"].isdigit() or not old["guid"].isdigit():
        blocks.append("Identità GUID incompleta.")
    if not disk.get("serial") or not disk.get("identity_verified") or sum(d.get("serial") == disk["serial"] for d in snapshot["disks"]) != 1:
        blocks.append("Identità fisica del disco da escludere non verificata.")
    if request.get("profile") != "produzione" or not request.get("hot_swap_confirmed"):
        blocks.append("Nel laboratorio senza hot swap usare lo spegnimento; in produzione verificare il vano.")
    if "zpool" not in snapshot.get("tools", []):
        blocks.append("Strumento zpool non disponibile.")
    argv = ["zpool", "offline", pool["name"], old["guid"]]
    return {"action": "offline", "request": deepcopy(request), "host_id": snapshot.get("host_id"),
        "pool": pool["name"], "pool_guid": pool["guid"], "role": pool["role"], "raid": raid_label(pool),
        "old_guid": old["guid"], "old_name": old["name"], "target": deepcopy(disk), "source": None,
        "new_member": None, "fingerprint": safety_fingerprint(snapshot), "topology_fingerprint": topology_fingerprint(pool), "blocks": blocks,
        "warnings": ["Esclude logicamente il membro e riduce la ridondanza. Non cancella dati, non esegue detach e non spegne elettricamente il vano."],
        "steps": [{"argv": argv, "command": command_text(argv), "phase": "offline",
                   "explanation": "Esclude il solo membro problematico identificato dal GUID, prima della rimozione a caldo."}],
        "guests": pool.get("guests", [])}


def anonymize(value):
    """Redact structured identity fields AND raw outputs, consistently per export."""
    aliases = {}
    counter = [0]

    def redact(item, key=""):
        if key in {"raw", "kernel_log", "status_raw", "guid_raw", "version_raw", "boot", "smart", "table"}:
            return "[omesso dall'esportazione anonimizzata]"
        if isinstance(item, dict):
            return {k: redact(v, k) for k, v in item.items()}
        if isinstance(item, list):
            return [redact(v, key) for v in item]
        safe = {"state", "role", "kind", "section", "fstype", "helper_version", "type", "collected_at"}
        if isinstance(item, str) and item and key not in safe:
            if item not in aliases:
                counter[0] += 1
                aliases[item] = f"REDACTED-{counter[0]:03}"
            return aliases[item]
        return item
    return redact(deepcopy(value))
