"""Escaped, Qt-free rendering of plans and audit details."""
import html
import json

from .workflow import next_step, state_label
from .core import disk_for_member, leaves, partition_for

e = lambda value: html.escape(str(value))


def disk_html(disk, snapshot=None):
    if not disk:
        return '<p>Disco non identificato.</p>'
    primary = disk.get('by_id') if disk.get('identity_verified') else 'Disk by ID non verificato'
    fields = [('Disk by ID', primary), ('Modello', disk.get('model')), ('Seriale', disk.get('serial')),
              ('Dispositivo Linux', disk.get('path')), ('WWN', disk.get('wwn') or 'Non disponibile')]
    for pool in (snapshot or {}).get('pools', []):
        for member in leaves(pool):
            if disk_for_member(snapshot, member) == disk:
                part = partition_for(disk, member['name']) or {}
                persistent = next((p for p in part.get('aliases', []) if p.startswith('/dev/disk/by-id/')), '')
                fields += [('Pool / stato', pool['name'] + ' / ' + member['state']), ('GUID ZFS', member.get('guid')),
                           ('Partizione ZFS', persistent or part.get('path') or member['name']), ('Dispositivo ZFS Linux', part.get('path') or member['name'])]
    for part in disk.get('partitions', []):
        if part.get('partuuid'):
            fields.append(('Partizione / PARTUUID', part['path'] + ' / ' + part['partuuid']))
    return '<table>' + ''.join('<tr><td><b>' + e(k) + '</b></td><td>' + e(v or 'N/D') + '</td></tr>' for k, v in fields) + '</table>' + ''.join('<p>' + e(w) + '</p>' for w in disk.get('identity_warnings', []))


def scan_summary(evidence):
    blank = evidence.get("blank", {})
    state = blank.get("state", "EMPTY" if blank.get("all_zero") else "NOT_STARTED")
    labels = {"NOT_STARTED": "Lettura integrale non avviata", "PROBE_FAILED": "Verifica firme non superata · lettura non avviata",
              "READ_ERROR": "Errore durante la lettura", "TIMEOUT": "Tempo di lettura esaurito",
              "INCOMPLETE": "Lettura incompleta", "NONZERO": "Dati non nulli rilevati", "EMPTY": "Contenuto ESP verificato: tutti i byte sono nulli"}
    message = labels.get(state, "Esito della lettura da verificare")
    if state not in {"NOT_STARTED", "PROBE_FAILED"}:
        message += " · Byte letti: " + str(blank.get("bytes_read", 0))
        if blank.get("expected_bytes"):
            message += " / " + str(blank["expected_bytes"])
    return message


def plan_html(plan, hostname="?"):
    body = f"<h2>{e(plan['pool'])} · {e(plan['role'])}</h2><p>{e(plan['raid'])}<br>Destinazione: <b>{e(plan['target']['serial'])}</b><br>{e(plan['target']['by_id'])}</p>"
    if plan.get('action') == 'uefi_test':
        esp = plan.get('expected_esp') or {}
        body = ('<h2>Test UEFI · ' + e(hostname) + '</h2><p><b>Voce: Boot' + e(plan.get('entry', {}).get('id', 'non verificata')) +
                '</b> · ' + e(plan.get('entry', {}).get('file', '')) + '<br>ESP: ' + e(esp.get('path', '')) +
                ' · UUID: ' + e(esp.get('uuid', '')) + '<br>PARTUUID: ' + e(esp.get('partuuid', '')) +
                '<br>GUID ZFS: ' + e(plan.get('binding', {}).get('member_guid', '')) + '</p>' + body)
        if plan.get('read_only'):
            body = '<p><b>Anteprima in sola lettura: nessun comando modificativo eseguibile.</b></p>' + body
    if plan.get("source"):
        body += "<p>Riferimento: " + e(plan["source"]["serial"]) + "</p>"
    body += disk_html(plan['target'], plan.get('before'))
    if plan["blocks"]:
        body += "<h3 style='color:#ffacb5'>Controlli non superati · nessuna esecuzione</h3><ul>" + "".join("<li>" + e(b) + "</li>" for b in plan["blocks"]) + "</ul>"
    else:
        body += "<h3 style='color:#7de0c4'>Controlli superati · leggi i comandi e approva</h3>"
    if plan.get("action") == "clean_boot":
        body += "<p><b>Confermare che gli UUID assenti non appartengano a dischi temporaneamente scollegati.</b></p>"
    if plan.get("action") == "initialize_esp":
        evidence = plan.get("initialization_evidence", {})
        body += "<h3>Inizializza nuova ESP dopo sostituzione disco</h3><p>" + e(scan_summary(evidence)) + "</p>"
        body += "<p>WWN: " + e(plan["target"].get("wwn", "")) + "<br>PARTUUID: " + e((plan.get("expected_esp") or {}).get("partuuid", ""))
        body += "<br>Intervento ZFS: " + e(plan.get("replacement_id") or "non verificato") + "</p>"
        body += "<p><b>Conferma separata richiesta per la formattazione FAT32.</b> UUID assenti da gestire separatamente: " + e(", ".join(plan.get("missing_uuids", [])) or "nessuno") + ".</p>"
        for check in evidence.get("blank", {}).get("checks", []):
            body += "<p>Verifica: <code>" + e(" ".join(check["argv"])) + "</code> · codice " + e(check.get("code", "non disponibile")) + "</p>"
            body += "<pre>" + e(check.get("out", "")) + e(check.get("err", "")) + "</pre>"
    body += "<h3>Comandi previsti</h3><ol>" + "".join(f"<li><pre>{e(s['command'])}</pre><p>{e(s['explanation'])}</p></li>" for s in plan["steps"]) + "</ol>"
    body += "<h3>Note</h3><ul>" + "".join("<li>" + e(w) + "</li>" for w in plan["warnings"]) + "</ul>"
    body += "<p>Validità: dieci minuti, con inventario invariato. ZFS ONLINE non certifica l'avvio autonomo.</p>"
    body += "<p>Nodo: " + e(hostname) + " · Pool GUID: " + e(plan["pool_guid"]) + "<br>Snapshot: " + e(plan["fingerprint"]) + "</p>"
    return body


def job_html(job):
    body = f"<h2>{e(job.get('pool', 'Intervento'))} · {e(state_label(job['state']))}</h2><p>ID {e(job['id'])} · {e(job['state'])}</p>"
    body += "<p><b>Prossimo passo:</b> " + e(next_step(job)) + "</p>"
    if job.get('target'):
        body += disk_html(job['target'], job.get('before'))
    if job.get('action') == 'uefi_test':
        body += '<p>Test del percorso UEFI, non prova di autonomia fisica.</p>'
        for label, value in [('Voce richiesta', job.get('entry')), ('Identità vincolata', job.get('binding')),
                             ('Prima del test', job.get('boot_before')), ('Dopo BootNext', job.get('boot_after_setting')),
                             ('Verifica post-riavvio', job.get('observation')), ('Conferme', job.get('confirmations')),
                             ('Intervento associato', job.get('replacement_id')), ('Errore connessione', job.get('connection_error'))]:
            body += '<p><b>' + e(label) + '</b></p><pre>' + e(json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (dict, list)) else value or 'Non disponibile') + '</pre>'
        for event in job.get('connection_events', []):
            body += '<p>Connessione ' + e(event['at']) + ': ' + e(event['error']) + '</p>'
    observation = job.get("system_observation")
    if observation:
        body += "<h3>Stato attuale del nodo: " + e(observation["state"]) + "</h3><p>" + e(observation["detail"]) + "<br>Rilevato: " + e(observation.get("checked_at")) + "</p>"
    for key in ("error", "verification_error", "result_message"):
        if job.get(key):
            body += "<p>" + e(job[key]) + "</p>"
    if job.get("zfs_state"):
        body += "<p>ZFS: " + e(job["zfs_state"]) + " · resilver: " + e(job.get("resilver_state", "DA_VERIFICARE")) + "</p>"
    if job.get("registry_cleanup", {}).get("state") == "REQUIRED":
        body += "<p>Pulizia UUID EFI separata richiesta: " + e(", ".join(job["registry_cleanup"]["missing_uuids"])) + "</p>"
    for disk in job.get("boot_validation", {}).get("disks", []):
        body += "<h4>EFI " + e(disk["serial"] or "membro assente") + "</h4><ul>"
        body += "".join("<li>" + e(name + ": " + c["state"] + " · " + c["detail"]) + "</li>" for name, c in disk["checks"].items()) + "</ul>"
    scan = (observation or {}).get("scan") or job.get("scan")
    if scan:
        body += "<pre>" + e(scan) + "</pre>"
        if not observation:
            body += "<p>Velocità: " + e(job.get("scan_rate", "Non disponibile")) + " · ETA ZFS: " + e(job.get("scan_eta", "Non disponibile")) + "</p>"
    body += "<h3>Registro dei comandi · esiti originali</h3>"
    for event in job.get("events", []):
        body += f"<h4>{e(event['state'])} · codice {e(event.get('code', 'non disponibile'))}</h4><pre>{e(event['command'])}</pre><pre>{e(event.get('out', ''))}</pre><pre>{e(event.get('err', ''))}</pre>"
    if job.get("boot_test"):
        body += "<p><b>Test avvio:</b> " + e(job["boot_test"]) + "</p>"
    return body
