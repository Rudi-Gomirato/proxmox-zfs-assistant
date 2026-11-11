"""Pure disk identity comparisons shared by inventory, planning and execution."""
import re


def normalize_wwn(value):
    value = str(value or "").strip().lower()
    raw = value.removeprefix("0x")
    if re.fullmatch(r"[0-9a-f:-]+", raw):
        return raw.replace(":", "").replace("-", "")
    return value


def same_disk_identity(left, right):
    return (all(left.get(k) and left.get(k) == right.get(k) for k in ("by_id", "serial"))
            and bool(normalize_wwn(left.get("wwn")))
            and normalize_wwn(left.get("wwn")) == normalize_wwn(right.get("wwn")))


def serial_alias(disk):
    """An independent by-id alias observed on this physical device."""
    serial = disk.get("serial", "")
    return next((p for p in disk.get("aliases", []) if serial and serial in p.rsplit("/", 1)[-1]
                 and p.startswith("/dev/disk/by-id/") and not p.rsplit("/", 1)[-1].startswith("wwn-")
                 and not re.search(r"-part\d+$", p)), "")


def wwn_peers(disks, target):
    value = normalize_wwn(target.get("wwn"))
    return [d for d in disks if value and normalize_wwn(d.get("wwn")) == value]


def composite_identity_verified(disks, target):
    if not (target.get("identity_verified") and target.get("path") and target.get("by_id")
            and target.get("serial") and normalize_wwn(target.get("wwn"))):
        return False
    if sum(d.get("serial") == target["serial"] for d in disks) != 1:
        return False
    peers = wwn_peers(disks, target)
    if len(peers) == 1:
        return True
    # Some hardware reports a shared WWN. It cannot establish identity alone.
    alias = serial_alias(target)
    return bool(alias and len({d.get("path") for d in peers}) == len(peers)
                and sum(alias in d.get("aliases", []) for d in disks) == 1)
