"""Conservative, read-only evidence for a newly created ESP, never a wipe API."""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import time
from pathlib import Path

from .core import SafetyError

MAX_ESP_BYTES = 4 * 1024 ** 3


class EspCheckError(SafetyError):
    def __init__(self, message, state, bytes_read=0, **details):
        super().__init__(message)
        self.evidence = {"state": state, "all_zero": False, "bytes_read": bytes_read, **details}


class EspInUse(SafetyError):
    """An observed consumer prevents advanced initialization."""


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
            metadata_only &= bool(path and (value == path or value.startswith("/dev/") and
                                  os.path.realpath(value) == os.path.realpath(path)))
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


def read_all_zero(stream, size, deadline, full=False):
    """Stop on the first nonzero chunk in standard mode. Bind an advanced plan to every byte."""
    total = count = 0
    first = None
    checksum = hashlib.sha256() if full else None
    def metrics(reason):
        return dict(partition_size_bytes=size, bytes_scanned=total, scan_status="complete" if total == size else "partial",
                    nonzero_detected=count > 0, nonzero_bytes_count=count, first_nonzero_offset=first,
                    total_nonzero_bytes=count if total == size else None, stop_reason=reason)
    while total < size:
        if time.monotonic() > deadline:
            raise EspCheckError("Lettura integrale ESP scaduta: nessuna formattazione.", "TIMEOUT", total, **metrics("timeout"))
        try:
            data = stream.read(min(4 * 1024 ** 2, size - total))
        except OSError as exc:
            raise EspCheckError("Errore di lettura ESP: " + str(exc), "READ_ERROR", total, **metrics("read_error")) from exc
        if not data:
            raise EspCheckError("Lettura ESP incompleta: nessuna formattazione.", "INCOMPLETE", total, **metrics("short_read"))
        nonzero = len(data) - data.count(b"\0")
        if nonzero and first is None:
            first = total + re.search(rb'[^\x00]', data).start()
        count += nonzero
        if checksum is not None:
            checksum.update(data)
        total += len(data)
        if nonzero and not full:
            raise EspCheckError("La ESP contiene byte non nulli: possibili dati da preservare, nessuna formattazione.",
                                "NONZERO", total, nonzero_bytes_found=count, **metrics("nonzero_detected"))
    return {"state": "NONZERO" if count else "EMPTY", "all_zero": count == 0, "bytes_read": total,
            "nonzero_bytes_found": count, "sha256": checksum.hexdigest() if checksum else None, **metrics("end")}


def blank_esp(path, expected_size, run, *, full=False):
    if not 0 < expected_size <= MAX_ESP_BYTES:
        raise SafetyError("Dimensione ESP non verificata o superiore al limite di scansione di 4 GiB.")
    checks = []
    try:
        for argv in (["blkid", "-p", "-o", "export", path], ["wipefs", "--json", path]):
            checks.append({"argv": argv, **run(argv)})
        verify_empty_signatures(checks[0], checks[1], path)
        result = read_block_device(path, expected_size, full=True) if full else read_block_device(path, expected_size)
        return {**result, "expected_bytes": expected_size, "checks": checks}
    except (SafetyError, OSError) as exc:
        error = exc if isinstance(exc, EspCheckError) else EspCheckError(str(exc), "READ_ERROR")
        error.evidence.update(expected_bytes=expected_size, checks=checks)
        if error is exc:
            raise
        raise error from exc


def read_block_device(path, expected_size, full=False):
    # Linux O_EXCL rejects mounted/in-use block devices; opening is read-only.
    import fcntl
    import struct
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_EXCL), "rb", buffering=0) as stream:
        if not stat.S_ISBLK(os.fstat(stream.fileno()).st_mode):
            raise SafetyError("La ESP non è un dispositivo a blocchi.")
        size = struct.unpack("Q", fcntl.ioctl(stream.fileno(), 0x80081272, bytes(8)))[0]  # BLKGETSIZE64
        if size != expected_size:
            raise SafetyError("Dimensione ESP cambiata: nessuna formattazione.")
        return read_all_zero(stream, size, time.monotonic() + 60, full)


def verify_unused(path, partuuid, proc=Path("/proc"), etc=Path("/etc")):
    """Check aliases by device number, including other processes' mount namespaces."""
    dev = os.stat(path).st_rdev
    number = f"{os.major(dev)}:{os.minor(dev)}"
    if not dev:
        raise SafetyError("Destinazione non riconosciuta come dispositivo a blocchi.")
    namespaces = set()
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            namespace = os.readlink(process / "ns/mnt")
            if namespace not in namespaces:
                mounts = (process / "mountinfo").read_text().splitlines()
                if any(line.split()[2] == number for line in mounts):
                    raise EspInUse("ESP montata in un namespace del nodo.")
                namespaces.add(namespace)
            for fd in (process / "fd").iterdir():
                try:
                    opened = os.stat(fd)
                except FileNotFoundError:
                    continue
                if stat.S_ISBLK(opened.st_mode) and opened.st_rdev == dev:
                    raise EspInUse("ESP aperta da un processo sul nodo.")
        except (FileNotFoundError, ProcessLookupError):
            if not process.exists() or not (process / "ns/mnt").exists():
                continue
            raise
    if not namespaces:
        raise SafetyError("Namespace di montaggio non verificabili.")
    for file, column in ((proc / "swaps", 0), (etc / "fstab", 0), (etc / "crypttab", 1)):
        if file == etc / "crypttab" and not file.exists():
            continue
        for line in file.read_text().splitlines():
            fields = line.partition("#")[0].split()
            if len(fields) <= column:
                continue
            source = fields[column]
            if (source.lower() == "partuuid=" + partuuid.lower() or
                    source.startswith("/dev/") and os.path.realpath(source) == os.path.realpath(path)):
                raise EspInUse("ESP in uso o configurata in " + str(file))
