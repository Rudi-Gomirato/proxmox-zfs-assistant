"""Residual ESP recovery uses synthetic block contents and a node-owned journal."""
import hashlib
import io
import time
from copy import deepcopy

import pytest

from pve_disk_assistant import core, esp, node, presentation
from test_boot import rebuilt
from test_esp_initialization import replacement, request
from test_node import approval


@pytest.fixture
def residual(replacement, monkeypatch):
    parent, snapshot, target, part = replacement
    peer = snapshot['disks'][0]
    peer['wwn'] = target['wwn']
    peer_part = peer['partitions'][1]
    snapshot['boot']['nvram'] = {'code': 0, 'current': '0003', 'order': ['0003'], 'entries': [
        {'id': '0003', 'active': True, 'partuuid': peer_part['partuuid'].lower(), 'file': 'efi/proxmox/grubx64.efi'}]}
    target['table'].update(id='disk-guid', partitions=[{'uuid': part['partuuid'], 'node': part['path'], 'type': core.ESP}])
    data = bytearray(4 * 1024 * 1024 + 5)
    data[19] = 31
    data[-1] = 5
    part['size'] = len(data)
    def read(path, size, run, full=False):
        assert path == target['by_id'] + '-part2' and size == len(data)
        return esp.read_all_zero(io.BytesIO(data), size, time.monotonic() + 15, full)
    monkeypatch.setattr(node, 'blank_esp', read)
    monkeypatch.setattr(node, 'verify_unused', lambda p, u: None)
    return snapshot, target, part, data


def advanced_plans():
    blocked = node.prepare(request())
    assert blocked['advanced_available'] and blocked['blocks']
    plan = node.prepare({**request(), 'advanced_from': blocked['id']})
    assert plan['esp_mode'] == 'advanced' and not plan['blocks'], plan['blocks']
    return blocked, plan


def consent(plan):
    return {**approval(plan), 'format_partuuid_confirmation': plan['expected_esp']['partuuid'],
            'accept_esp_loss': True, 'confirm_target': True}


def test_advanced_scans_bind_full_content_and_preserve_blocked_preview(residual):
    _, _, _, data = residual
    blocked, plan = advanced_plans()
    scan = blocked['initialization_evidence']['blank']
    assert scan['scan_status'] == 'partial' and scan['bytes_scanned'] == 4 * 1024 * 1024
    assert scan['first_nonzero_offset'] == 19 and scan['nonzero_bytes_count'] == 1
    assert 'parziale' in presentation.scan_summary(blocked['initialization_evidence'])
    assert blocked['esp_class'] == plan['esp_class'] == 'UNKNOWN_RESIDUAL_DATA'
    full = plan['initialization_evidence']['blank']
    assert full['scan_status'] == 'complete' and full['nonzero_bytes_count'] == 2
    assert full['sha256'] == hashlib.sha256(data).hexdigest()
    assert plan['id'] != blocked['id'] and node.load_record(blocked['id']) == blocked
    assert [s['argv'][1] for s in plan['steps']] == ['format', 'init', 'refresh', 'status']
    data[-1] ^= 128
    with pytest.raises(core.SafetyError, match='Inventario cambiato'):
        node.submit(consent(plan))


@pytest.mark.parametrize('change', ['accept_esp_loss', 'confirm_target', 'serial_confirmation', 'expires', 'mounted', 'peer'])
def test_advanced_authorization_failures(residual, monkeypatch, change):
    snapshot, _, _, _ = residual
    _, plan = advanced_plans()
    signed = consent(plan)
    if change in ('accept_esp_loss', 'confirm_target'):
        signed[change] = False
    elif change == 'serial_confirmation':
        signed[change] += ' '
    elif change == 'expires':
        monkeypatch.setattr(node.time, 'time', lambda: plan['expires_at'] + 1)
    elif change == 'mounted':
        def in_use(*args):
            raise esp.EspInUse('ESP in uso in un altro namespace')
        monkeypatch.setattr(node, 'verify_unused', in_use)
        blocked = node.prepare(request())
        assert blocked['esp_class'] == 'MOUNTED_OR_IN_USE' and not blocked['advanced_available']
    else:
        snapshot['boot']['nvram']['current'] = '0001'
    with pytest.raises(core.SafetyError):
        node.submit(signed)
    assert node.load_record(plan['id'])['state'] == 'PREVIEW'


def test_advanced_executes_once_and_verifies(residual, monkeypatch):
    snapshot, target, part, _ = residual
    _, plan = advanced_plans()
    node.submit(consent(plan))
    with pytest.raises(core.SafetyError):
        node.submit(consent(plan))
    executed = []
    def run(argv, **kwargs):
        executed.append(argv)
        if argv[1] == 'format':
            fresh = rebuilt()
            configured = fresh['disks'][-1]['partitions'][1]
            part.update(uuid=configured['uuid'], fstype='vfat')
            snapshot['boot'].update(esps=deepcopy(fresh['boot']['esps']),
                                    registered_uuids=fresh['boot']['registered_uuids'], status=fresh['boot']['status'])
        return {'code': 0, 'err': '', 'out': ''}
    monkeypatch.setattr(node, 'run', run)
    node.perform_job(plan['id'])
    node.perform_job(plan['id'])
    assert [a[1] for a in executed] == ['format', 'init', 'refresh', 'status']
    assert executed[0][2] == target['by_id'] + '-part2'
    result = node.get_status(plan['id'])
    assert result['state'] == 'BOOT_TEST_PENDING' and result['formatted_uuid'] == part['uuid']
    assert result['format_approved']['advanced_from'] == plan['request']['advanced_from']


def test_changed_residuals_after_approval_stop_worker(residual, monkeypatch):
    _, _, _, data = residual
    _, plan = advanced_plans()
    node.submit(consent(plan))
    data[-1] ^= 1
    monkeypatch.setattr(node, 'run', lambda *args, **kw: pytest.fail("No writes allowed"))
    node.perform_job(plan['id'])
    assert node.load_record(plan['id'])['state'] == 'BLOCKED'


@pytest.mark.parametrize('state,classification', [('TIMEOUT', 'SCAN_INCOMPLETE'), ('INCOMPLETE', 'SCAN_INCOMPLETE'),
                                                  ('READ_ERROR', 'DEVICE_ERROR')])
def test_failed_advanced_read_never_unlocks(residual, monkeypatch, state, classification):
    blocked = node.prepare(request())
    def interrupted(*args, **kwargs):
        raise esp.EspCheckError('Scansione non conclusa', state, 1024)
    monkeypatch.setattr(node, 'blank_esp', interrupted)
    plan = node.prepare({**request(), 'advanced_from': blocked['id']})
    assert plan['blocks'] and plan['esp_class'] == classification and not plan['advanced_available']
    with pytest.raises(core.SafetyError):
        node.submit(consent(plan))
    assert node.load_record(plan['id'])['approval_refusals']


def test_recognized_filesystem_never_eligible_for_advanced_format(residual):
    snapshot, _, part, _ = residual
    part['fstype'] = 'ext4'
    snapshot['boot']['esps'][part['path']]['probe']['TYPE'] = 'ext4'
    plan = node.prepare(request())
    assert plan['blocks'] and plan['esp_class'] == 'RECOGNIZED_OTHER_FS'
    assert not plan['advanced_available']
    with pytest.raises(core.SafetyError):
        node.prepare({**request(), 'advanced_from': plan['id']})


@pytest.mark.parametrize('use', ['namespace', 'open_fd', 'fstab', 'none'])
def test_indirect_usage_checks(use, tmp_path, monkeypatch):
    import os
    import stat
    from types import SimpleNamespace
    proc, etc = tmp_path / 'proc', tmp_path / 'etc'
    files = proc / '65' / 'fd'
    files.mkdir(parents=True)
    etc.mkdir()
    (files.parent / 'mountinfo').write_text('12 1 8:2 / /boot rw - vfat /dev/disk/by-id/example rw' if use == 'namespace' else '')
    (etc / 'fstab').write_text('PARTUUID=TARGET /boot vfat defaults 0 2' if use == 'fstab' else '')
    (proc / 'swaps').write_text('Filename Type Size Used Priority\n')
    (files / '4').touch()
    real_stat = os.stat
    def fake_stat(path, *args, **kwargs):
        if str(path) == '/dev/test-esp' or use == 'open_fd' and path == files / '4':
            return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=2050)
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(esp.os, 'stat', fake_stat)
    monkeypatch.setattr(esp.os, 'major', lambda d: 8, raising=False)
    monkeypatch.setattr(esp.os, 'minor', lambda d: 2, raising=False)
    monkeypatch.setattr(esp.os, 'readlink', lambda p: 'mnt:[23]')
    if use == 'none':
        esp.verify_unused('/dev/test-esp', 'target', proc, etc)
    else:
        with pytest.raises(esp.EspInUse):
            esp.verify_unused('/dev/test-esp', 'target', proc, etc)
