"""Pure disk identity comparisons shared by inventory, planning and execution."""
import re


def normalize_wwn(value):
    value = str(value or "").strip().lower()
    raw = value.removeprefix("0x")
    if re.fullmatch(r"[0-9a-f:-]+", raw):
        return raw.replace(":", "").replace("-", "")
    return value


def same_disk_identity(left, right):
    same_alias = (left.get('by_id') and right.get('by_id') and
                  (left['by_id'] in [right['by_id'], *right.get('aliases', [])] or
                   right['by_id'] in [left['by_id'], *left.get('aliases', [])]))
    return (same_alias and left.get('serial') and left['serial'] == right.get('serial')
            and bool(normalize_wwn(left.get("wwn")))
            and normalize_wwn(left.get("wwn")) == normalize_wwn(right.get("wwn")))


def serial_alias(disk):
    """An independent by-id alias observed on this physical device."""
    serial = disk.get("serial", "")
    if not serial:
        return ""
    suffix = re.compile(r"(?:^|[-_])" + re.escape(serial) + r"(?:_\d+)?$")
    return next((path for path in disk.get("aliases", [])
                 if path.startswith("/dev/disk/by-id/")
                 and not path.rsplit("/", 1)[-1].startswith("wwn-")
                 and not re.search(r"-part\d+$", path)
                 and suffix.search(path.rsplit("/", 1)[-1])), "")


def wwn_peers(disks, target):
    value = normalize_wwn(target.get("wwn"))
    return [d for d in disks if value and normalize_wwn(d.get("wwn")) == value]


def composite_identity_verified(disks, target):
    if not (physical_identity_verified(disks, target) and target.get("path") and target.get("by_id")
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


def physical_identity_verified(disks, target):
    """WWN is optional, but neither a duplicate WWN nor /dev/sdX proves identity."""
    if not target or not target.get('identity_verified') or not (target.get('by_id') or '').startswith('/dev/disk/by-id/') or not target.get('serial'):
        return False
    if sum(d.get('serial') == target['serial'] for d in disks) != 1:
        return False
    if sum(target['by_id'] in [d.get('by_id'), *d.get('aliases', [])] for d in disks) != 1:
        return False
    peers = wwn_peers(disks, target)
    if len(peers) == 1:
        return True
    alias = serial_alias(target)
    return bool(alias and alias == target['by_id'] and sum(alias in d.get('aliases', []) for d in disks) == 1)


def annotate_identities(disks):
    """Annotate freshly resolved inventory; never synthesize an alias."""
    for disk in disks:
        warnings = []
        if len(wwn_peers(disks, disk)) > 1:
            warnings.append('WWN duplicato rilevato. L’identificazione tramite WWN non è affidabile per questi dispositivi.')
        if not physical_identity_verified(disks, disk):
            warnings.append('Identità fisica assente o ambigua: modifiche bloccate, diagnostica disponibile.')
            disk['identity_verified'] = False
        for part in disk.get('partitions', []):
            partuuid = part.get('partuuid', '').lower()
            if partuuid and sum(p.get('partuuid', '').lower() == partuuid for d in disks for p in d.get('partitions', [])) != 1:
                warnings.append('PARTUUID duplicato: associazione della partizione ambigua.')
                disk['identity_verified'] = False
        disk['identity_warnings'] = warnings
    return disks
