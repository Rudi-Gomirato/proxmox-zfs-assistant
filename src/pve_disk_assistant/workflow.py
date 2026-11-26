"""Shared workflow vocabulary and read-only observations; never authorizes writes."""
from .core import GOOD, disk_for_member, disk_for, partition_for, leaves
from .identity import physical_identity_verified, normalize_wwn
from .uefi import LABELS as UEFI_LABELS, PENDING as UEFI_PENDING

ACTIVE_STATES = {"QUEUED", "RUNNING", "MONITORING", "WAITING_RESILVER"}
REVIEW_STATES = {"MONITORING", "WAITING_RESILVER", "BOOT_REVIEW_REQUIRED", "BOOT_TEST_REQUIRED",
                 "BOOT_TEST_PENDING", "ESP_INITIALIZATION_REQUIRED", "ZFS_REVIEW_REQUIRED"}

STATE_LABELS = {
    "PREVIEW": "Piano da approvare", "QUEUED": "In coda", "RUNNING": "Comandi in esecuzione",
    "MONITORING": "Verifica in corso", "WAITING_RESILVER": "Ricostruzione ZFS in corso",
    "ESP_INITIALIZATION_REQUIRED": "ZFS pronto · configura EFI", "ESP_INITIALIZATION_LINKED": "Prosegui nell'intervento EFI collegato",
    "BOOT_REVIEW_REQUIRED": "Avvio EFI da verificare", "BOOT_TEST_PENDING": "Prova fisica di avvio richiesta",
    "EFI_REPAIR_REQUIRED": "FAT32 rilevata dopo interruzione · riprendi configurazione EFI",
    "BOOT_TEST_REQUIRED": "Prova fisica di avvio richiesta", "ZFS_REVIEW_REQUIRED": "Stato ZFS da verificare",
    "COMPLETED": "Verifiche completate", "FAILED": "Comando non riuscito", "UNKNOWN": "Esito da riconciliare",
    "BLOCKED": "Controlli non superati", "FAILED_TO_START": "Esecuzione non avviata",
    "READY_FOR_REMOVAL": "Esclusione logica verificata",
}


def state_label(state):
    return UEFI_LABELS.get(state, STATE_LABELS.get(state, state or "Non disponibile"))


def should_refresh(job):
    return (job.get("state") in REVIEW_STATES | ACTIVE_STATES | UEFI_PENDING
            or job.get("action") in {"replace", "initialize_esp"} and job.get("state") in {"FAILED", "UNKNOWN"}
            or job.get("role") in {"BOOT", "MISTO"} and job.get("state") == "COMPLETED")


def replacement_phases(job):
    """Derive progress from the journal, including records written by earlier helpers."""
    if job.get("action") != "replace":
        return {}
    state = job.get("state")
    phases = {"Approvazione": "In attesa di approvazione", "Sostituzione ZFS": "Non iniziata",
              "Resilver": "Non iniziata", "Configurazione EFI": "Non iniziata", "Avvio fisico": "Non iniziata"}
    if job.get("approved_at"):
        phases["Approvazione"] = "Completata e verificata"
    if job.get("blocks") or state == "BLOCKED":
        phases["Approvazione"] = "In attesa di intervento"
    if state == "RUNNING":
        phases["Sostituzione ZFS"] = "In esecuzione"
    if state in {"FAILED", "FAILED_TO_START", "UNKNOWN"}:
        phases["Sostituzione ZFS"] = "Interrotta" if state == "UNKNOWN" else "Fallita"
    if any(e.get("phase") == "replace" and e.get("state") == "OK" for e in job.get("events", [])):
        phases["Sostituzione ZFS"] = "In attesa di verifica"
        phases["Resilver"] = "In attesa di verifica"
    if job.get("zfs_state") == "IN_CORSO":
        phases["Resilver"] = "In esecuzione"
    if job.get("zfs_state") == "OK":
        phases["Sostituzione ZFS"] = phases["Resilver"] = "Completata e verificata"
    if job.get("role") == "DATI_VM":
        phases["Configurazione EFI"] = phases["Avvio fisico"] = "Non applicabile"
    elif state in {"BOOT_TEST_PENDING", "COMPLETED"} and job.get("boot_validation", {}).get("configuration_ready"):
        phases["Configurazione EFI"] = "Completata e verificata"
        phases["Avvio fisico"] = ("Completata e verificata" if state == "COMPLETED" and
                                  job.get("boot_test") == "VERIFICATO_DALL_OPERATORE" else "In attesa di intervento")
    elif state in {"ESP_INITIALIZATION_REQUIRED", "BOOT_REVIEW_REQUIRED", "ESP_INITIALIZATION_LINKED"}:
        phases["Configurazione EFI"] = "In attesa di intervento"
    return phases


def next_step(job):
    state = job.get("state")
    if job.get('action') == 'uefi_test':
        return ('Approva separatamente il riavvio dopo aver fermato VM/CT. Console e recupero devono essere disponibili.' if state == 'UEFI_READY_REBOOT' else
                'Attendi la riconnessione o usa Verifica post-riavvio. Nessun comando modificativo viene ripetuto.' if state in UEFI_PENDING else
                'Test UEFI distinto dall’autonomia fisica: consulta lo storico in Avvio EFI.')
    if state in {"FAILED", "UNKNOWN", "BLOCKED", "FAILED_TO_START"}:
        return "Aggiorna lo stato reale e consulta il comando non riuscito. Non ripetere GPT, replace o format."
    if state == "EFI_REPAIR_REQUIRED":
        return "Prosegui con la ESP rilevata. Rivedi e approva il ripristino della FAT32 esistente."
    if state == "ESP_INITIALIZATION_REQUIRED":
        return "Continua con la nuova ESP: il nodo preparerà un piano separato da approvare."
    if state == "ESP_INITIALIZATION_LINKED":
        return "Apri l'intervento EFI collegato: " + job.get("initialization_job", "non disponibile")
    if state in {"BOOT_TEST_PENDING", "BOOT_TEST_REQUIRED"}:
        return "Registra gli avvii autonomi, uno per disco, risincronizzando il mirror fra le due prove."
    if job.get("registry_cleanup", {}).get("state") == "REQUIRED":
        return "Apri Avvio EFI e verifica il piano separato di pulizia degli UUID assenti."
    if state in ACTIVE_STATES:
        return "Attendi l'aggiornamento dal nodo. Chiudere l'app non annulla i comandi già avviati."
    if state == "COMPLETED":
        return "Verifiche concluse. Puoi esportare il rapporto dalla panoramica."
    return "Aggiorna la diagnostica e verifica i prerequisiti prima di preparare un nuovo piano."


def observe_replacement(job, snapshot):
    """Report current ZFS facts beside a failed command, without rewriting history."""
    result = {"state": "UNCONFIRMED", "checked_at": snapshot.get("collected_at"),
              "detail": "Associazione al ricambio non confermata; nessuna ripetizione automatica."}
    if not snapshot.get("complete") or snapshot.get("issues") or snapshot.get("host_id") != job.get("host_id"):
        return result
    pool = next((p for p in snapshot.get("pools", []) if p["guid"] == job.get("pool_guid")), None)
    if not pool or pool.get("warnings"):
        return result
    result.update(pool_state=pool["state"], scan=pool.get("scan", ""), scan_percent=pool.get("scan_percent"))
    expected = job.get("target") or {}
    pairs = [(m, disk_for_member(snapshot, m)) for m in leaves(pool)]
    matched = [(m, d) for m, d in pairs if physical_identity_verified(snapshot["disks"], d)
               and d.get("serial") == expected.get("serial") and expected.get("by_id") in [d.get("by_id"), *d.get("aliases", [])]
               and (not expected.get("wwn") or normalize_wwn(d.get("wwn")) == normalize_wwn(expected["wwn"]))
               and disk_for(snapshot, job.get("new_member")) == d
               and partition_for(d, m["name"]) == partition_for(d, job["new_member"]) and m.get("guid") and m["guid"] != job.get("old_guid")]
    if len(matched) != 1:
        return result
    member, disk = matched[0]
    expected_esp = job.get("expected_esp") or {}
    if expected_esp and not any(p.get("partuuid", "").lower() == expected_esp.get("partuuid", "").lower()
                                for p in disk.get("partitions", [])):
        return result
    result["member_guid"] = member["guid"]
    if "resilver in progress" in pool.get("scan", ""):
        result.update(state="RESILVERING", detail="ZFS sta ricostruendo sul ricambio identificato. L'errore del comando resta nel registro; la causa non viene dedotta.")
    elif pool["state"] == "ONLINE" and pool.get("errors") == "No known data errors" and all(
            m["state"] in GOOD and len(m.get("errors", [])) == 3 and not any(m["errors"]) for m, _ in pairs):
        result.update(state="ONLINE", detail="Pool e ricambio ONLINE senza errori ZFS. Questo rilievo non certifica la provenienza della sostituzione né l'avvio EFI.")
    return result
