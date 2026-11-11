"""Shared workflow vocabulary and read-only observations; never authorizes writes."""
from .core import GOOD, disk_for_member, leaves
from .identity import normalize_wwn

ACTIVE_STATES = {"QUEUED", "RUNNING", "MONITORING", "WAITING_RESILVER"}
REVIEW_STATES = {"MONITORING", "WAITING_RESILVER", "BOOT_REVIEW_REQUIRED", "BOOT_TEST_REQUIRED",
                 "BOOT_TEST_PENDING", "ESP_INITIALIZATION_REQUIRED", "ZFS_REVIEW_REQUIRED"}

STATE_LABELS = {
    "PREVIEW": "Piano da approvare", "QUEUED": "In coda", "RUNNING": "Comandi in esecuzione",
    "MONITORING": "Verifica in corso", "WAITING_RESILVER": "Ricostruzione ZFS in corso",
    "ESP_INITIALIZATION_REQUIRED": "ZFS pronto · configura EFI", "ESP_INITIALIZATION_LINKED": "Prosegui nell'intervento EFI collegato",
    "BOOT_REVIEW_REQUIRED": "Avvio EFI da verificare", "BOOT_TEST_PENDING": "Prova fisica di avvio richiesta",
    "BOOT_TEST_REQUIRED": "Prova fisica di avvio richiesta", "ZFS_REVIEW_REQUIRED": "Stato ZFS da verificare",
    "COMPLETED": "Verifiche completate", "FAILED": "Comando non riuscito", "UNKNOWN": "Esito da riconciliare",
    "BLOCKED": "Controlli non superati", "FAILED_TO_START": "Esecuzione non avviata",
    "READY_FOR_REMOVAL": "Esclusione logica verificata",
}


def state_label(state):
    return STATE_LABELS.get(state, state or "Non disponibile")


def should_refresh(job):
    return (job.get("state") in REVIEW_STATES | ACTIVE_STATES
            or job.get("action") == "replace" and job.get("state") in {"FAILED", "UNKNOWN"}
            or job.get("role") in {"BOOT", "MISTO"} and job.get("state") == "COMPLETED")


def next_step(job):
    state = job.get("state")
    if state in {"FAILED", "UNKNOWN", "BLOCKED", "FAILED_TO_START"}:
        return "Aggiorna lo stato reale e consulta il comando non riuscito. Non ripetere GPT, replace o format."
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
    matched = [(m, d) for m, d in pairs if d and d.get("identity_verified")
               and d.get("serial") == expected.get("serial") and d.get("by_id") == expected.get("by_id")
               and (not expected.get("wwn") or normalize_wwn(d.get("wwn")) == normalize_wwn(expected["wwn"]))
               and m.get("name") == job.get("new_member") and m.get("guid") and m["guid"] != job.get("old_guid")]
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
