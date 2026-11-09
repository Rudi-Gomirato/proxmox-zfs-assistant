"""Conservative, read-only evidence for a newly created ESP, never a wipe API."""
from __future__ import annotations

import json
import os
import stat
import time

from .core import SafetyError

MAX_ESP_BYTES = 4 * 1024 ** 3


def verify_empty_signatures(probe, wipe):
    """blkid may report GPT PART_ENTRY metadata with code 0 on an empty ESP."""
    allowed = {"PART_ENTRY_SCHEME", "PART_ENTRY_NAME", "PART_ENTRY_UUID", "PART_ENTRY_TYPE",
               "PART_ENTRY_FLAGS", "PART_ENTRY_NUMBER", "PART_ENTRY_OFFSET", "PART_ENTRY_SIZE",
               "PART_ENTRY_DISK"}
    lines = probe.get("out", "").splitlines()
    metadata_only = all("=" in line and line.split("=", 1)[0] in allowed for line in lines)
    try:
        signatures = json.loads(wipe.get("out", "")).get("signatures")
    except (ValueError, AttributeError):
        signatures = None
    if (probe.get("code") not in {0, 2} or (probe.get("code") == 0 and not lines)
            or not metadata_only or probe.get("err", "").strip() or wipe.get("code") != 0
            or wipe.get("err", "").strip() or signatures != []):
        raise SafetyError("Firme, dati o esito della scansione non verificabili: formattazione annullata.")


def read_all_zero(stream, size, deadline):
    """Every byte must be read. Sampling and an absent FSTYPE are insufficient."""
    total = 0
    while total < size:
        if time.monotonic() > deadline:
            raise SafetyError("Lettura integrale ESP scaduta: nessuna formattazione.")
        data = stream.read(min(4 * 1024 ** 2, size - total))
        if not data:
            raise SafetyError("Lettura ESP incompleta: nessuna formattazione.")
        if data.count(b"\0") != len(data):
            raise SafetyError("La ESP contiene byte non nulli: possibili dati da preservare, nessuna formattazione.")
        total += len(data)
    return {"all_zero": True, "bytes_read": total}


def blank_esp(path, expected_size, run):
    if not 0 < expected_size <= MAX_ESP_BYTES:
        raise SafetyError("Dimensione ESP non verificata o superiore al limite di scansione di 4 GiB.")
    verify_empty_signatures(run(["blkid", "-p", "-o", "export", path]),
                            run(["wipefs", "--json", path]))
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
