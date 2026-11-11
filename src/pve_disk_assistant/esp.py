"""Conservative, read-only evidence for a newly created ESP, never a wipe API."""
from __future__ import annotations

import json
import os
import stat
import time

from .core import SafetyError

MAX_ESP_BYTES = 4 * 1024 ** 3


class EspCheckError(SafetyError):
    def __init__(self, message, state, bytes_read=0, **details):
        super().__init__(message)
        self.evidence = {"state": state, "all_zero": False, "bytes_read": bytes_read, **details}


def verify_empty_signatures(probe, wipe, path=None):
    """blkid may report GPT PART_ENTRY metadata with code 0 on an empty ESP."""
    allowed = {"PART_ENTRY_SCHEME", "PART_ENTRY_NAME", "PART_ENTRY_UUID", "PART_ENTRY_TYPE",
               "PART_ENTRY_FLAGS", "PART_ENTRY_NUMBER", "PART_ENTRY_OFFSET", "PART_ENTRY_SIZE",
               "PART_ENTRY_DISK"}
    lines = probe.get("out", "").splitlines()
    tags = {}
    metadata_only = True
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key in tags:
            metadata_only = False
        if key == "DEVNAME":
            # util-linux emits DEVNAME before the GPT tags in export mode.
            metadata_only &= bool(path and value == path)
        elif key not in allowed:
            metadata_only = False
        tags[key] = value
    try:
        signatures = json.loads(wipe.get("out", "")).get("signatures")
    except (ValueError, AttributeError):
        signatures = None
    if (probe.get("code") not in {0, 2} or (probe.get("code") == 0 and not lines)
            or not metadata_only or probe.get("err", "").strip() or wipe.get("code") != 0
            or wipe.get("err", "").strip() or signatures != []):
        reason = ("Firma di filesystem o metadati non previsti in blkid." if not metadata_only else
                  "wipefs rileva firme o non restituisce un elenco valido." if signatures != [] else
                  "Codice di uscita o diagnostica degli strumenti non verificabile.")
        raise EspCheckError(reason + " Lettura integrale non avviata; formattazione annullata.", "PROBE_FAILED")


def read_all_zero(stream, size, deadline):
    """Every byte must be read. Sampling and an absent FSTYPE are insufficient."""
    total = 0
    while total < size:
        if time.monotonic() > deadline:
            raise EspCheckError("Lettura integrale ESP scaduta: nessuna formattazione.", "TIMEOUT", total)
        try:
            data = stream.read(min(4 * 1024 ** 2, size - total))
        except OSError as exc:
            raise EspCheckError("Errore di lettura ESP: " + str(exc), "READ_ERROR", total) from exc
        if not data:
            raise EspCheckError("Lettura ESP incompleta: nessuna formattazione.", "INCOMPLETE", total)
        nonzero = len(data) - data.count(b"\0")
        total += len(data)
        if nonzero:
            raise EspCheckError("La ESP contiene byte non nulli: possibili dati da preservare, nessuna formattazione.",
                                "NONZERO", total, nonzero_bytes_found=nonzero)
    return {"state": "EMPTY", "all_zero": True, "bytes_read": total, "nonzero_bytes_found": 0}


def blank_esp(path, expected_size, run):
    if not 0 < expected_size <= MAX_ESP_BYTES:
        raise SafetyError("Dimensione ESP non verificata o superiore al limite di scansione di 4 GiB.")
    checks = []
    try:
        for argv in (["blkid", "-p", "-o", "export", path], ["wipefs", "--json", path]):
            checks.append({"argv": argv, **run(argv)})
        verify_empty_signatures(checks[0], checks[1], path)
        result = read_block_device(path, expected_size)
        return {**result, "expected_bytes": expected_size, "checks": checks}
    except (SafetyError, OSError) as exc:
        error = exc if isinstance(exc, EspCheckError) else EspCheckError(str(exc), "READ_ERROR")
        error.evidence.update(expected_bytes=expected_size, checks=checks)
        if error is exc:
            raise
        raise error from exc


def read_block_device(path, expected_size):
    # Linux O_EXCL rejects mounted/in-use block devices; opening is read-only.
    import fcntl
    import struct
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_EXCL), "rb", buffering=0) as stream:
        if not stat.S_ISBLK(os.fstat(stream.fileno()).st_mode):
            raise SafetyError("La ESP non è un dispositivo a blocchi.")
        size = struct.unpack("Q", fcntl.ioctl(stream.fileno(), 0x80081272, bytes(8)))[0]  # BLKGETSIZE64
        if size != expected_size:
            raise SafetyError("Dimensione ESP cambiata: nessuna formattazione.")
        return read_all_zero(stream, size, time.monotonic() + 60)
