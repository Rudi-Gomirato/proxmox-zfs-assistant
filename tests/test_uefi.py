"""0.5.0 acceptance scenarios; synthetic identities, never a real node."""
import contextlib
import io
import json
import time
from copy import deepcopy

import pytest

from pve_disk_assistant import boot, core, demo, identity, node, uefi, uefi_service
from pve_disk_assistant.store import Store


def inventory():
    result = demo.snapshot()
    result.pop('demo')
    demo.add_uefi_demo(result)
    for d in result['disks'][:2]:
        d['wwn'] = '0x5000000000000001'  # Deliberately shared, different serials.
    identity.annotate_identities(result['disks'])
    return result


def request(snapshot):
    return {'pool_guid': snapshot['pools'][0]['guid'], 'target': snapshot['disks'][0]['by_id']}


def approve(plan):
    return {'id': plan['id'], 'approval_digest': plan['approval_digest'],
            'serial_confirmation': plan['target']['serial'], 'confirmations': {k: True for k in uefi.CONFIRMATIONS}}


def nvram_text(snapshot):
    data = snapshot['boot']['nvram']
    lines = ['BootCurrent: ' + data['current'], 'BootOrder: ' + ','.join(data['order'])]
    if data.get('next'):
        lines.append('BootNext: ' + data['next'])
    lines += ['Boot' + e['id'] + '* Synthetic HD(2,GPT,' + e['partuuid'] + ',0x800,0x10000)/File(' + e['file'] + ')' for e in data['entries']]
    return '\n'.join(lines)


@pytest.fixture
def service(monkeypatch, tmp_path):
    snapshot = inventory()
    calls = []
    monkeypatch.setattr(node, 'state_dir', lambda: tmp_path)
    monkeypatch.setattr(node, 'locked', lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(node, 'collect', lambda: deepcopy(snapshot))
    monkeypatch.setattr(node, 'verify_target', lambda plan: None)
    monkeypatch.setattr(node, 'power_preflight', lambda snap: None)
    def run(argv):
        calls.append(argv)
        if argv[:2] == ['efibootmgr', '-n']:
            snapshot['boot']['nvram']['next'] = argv[2]
        return {'code': 0, 'out': nvram_text(snapshot) if argv == ['efibootmgr', '-v'] else '', 'err': ''}
    monkeypatch.setattr(node, 'run', run)
    return snapshot, calls, tmp_path


def test_duplicate_wwn_uses_unique_serial_alias_and_persists_warning():
    snap = inventory()
    assert all(d['identity_verified'] for d in snap['disks'])
    assert all('WWN duplicato' in d['identity_warnings'][0] for d in snap['disks'][:2])
    assert not uefi.preflight(snap, request(snap))['blocks']
    assert node.stable_id(['/dev/disk/by-id/wwn-shared', '/dev/disk/by-id/ata-model-SERIAL'], 'SERIAL').endswith('ata-model-SERIAL')


@pytest.mark.parametrize('change', [
    lambda s: s['disks'][1].update(serial=s['disks'][0]['serial']),
    lambda s: s['disks'][0].update(identity_verified=False),
    lambda s: s['disks'][0].update(aliases=['/dev/disk/by-id/wwn-shared'], by_id='/dev/disk/by-id/wwn-shared'),
    lambda s: s['disks'][1]['partitions'][1].update(partuuid=s['disks'][0]['partitions'][1]['partuuid']),
    lambda s: s['boot']['nvram']['entries'][0].update(partuuid='wrong-partuuid'),
    lambda s: s['boot']['esps']['/dev/sda2'].update(files=[]),
    lambda s: s['boot'].update(firmware_mode='BIOS'),
    lambda s: s['boot'].update(status='Unknown bootloader'),
    lambda s: s['pools'][0].update(state='DEGRADED'),
    lambda s: s['pools'][0].update(scan='resilver in progress'),
    lambda s: s['boot']['nvram'].update(ambiguous=True),
    lambda s: s.update(kernel='unknown'),
])
def test_incomplete_or_ambiguous_preflight_blocks_writes(change):
    snap = inventory(); change(snap)
    assert uefi.preflight(snap, request(snap))['blocks']


def test_multiple_valid_entries_require_explicit_selection():
    snap = inventory()
    snap['boot']['nvram']['entries'].append({**snap['boot']['nvram']['entries'][0], 'id': '000F'})
    assert uefi.preflight(snap, request(snap))['blocks']
    assert not uefi.preflight(snap, {**request(snap), 'entry_id': '000F'})['blocks']


def test_plan_is_read_only_and_requires_all_confirmations(service):
    snap, calls, directory = service
    preview = uefi_service.prepare(request(snap), read_only=True)
    assert not list(directory.glob('*.json')) and not calls
    plan = uefi_service.prepare(request(snap))
    assert not calls
    for key in uefi.CONFIRMATIONS:
        payload = approve(plan); payload['confirmations'][key] = False
        with pytest.raises(core.SafetyError):
            uefi_service.set_next(payload)
    assert not calls


def test_bootnext_verified_without_reboot_and_never_repeated(service):
    snap, calls, _ = service
    plan = uefi_service.prepare(request(snap))
    result = uefi_service.set_next(approve(plan))
    assert result['state'] == 'UEFI_READY_REBOOT'
    assert calls == [['efibootmgr', '-n', '0001'], ['efibootmgr', '-v']]
    assert result['boot_before']['order'] == result['boot_after_setting']['order']
    with pytest.raises(core.SafetyError):
        uefi_service.set_next(approve(plan))
    with pytest.raises(core.SafetyError):
        uefi_service.reboot({'id': plan['id']})
    assert len(calls) == 2


@pytest.mark.parametrize('mode', ['refused', 'timeout', 'order_changed'])
def test_bootnext_failure_is_recorded_and_never_replayed(service, monkeypatch, mode):
    snap, calls, _ = service
    plan = uefi_service.prepare(request(snap))
    def fail(argv):
        calls.append(argv)
        if argv[1] == '-n':
            if mode == 'timeout':
                raise core.SafetyError('Timeout simulated')
            if mode == 'order_changed':
                snap['boot']['nvram'].update(next='0001', order=['0002', '0001'])
            return {'code': 1 if mode == 'refused' else 0, 'out': '', 'err': 'simulated'}
        return {'code': 0, 'out': nvram_text(snap), 'err': ''}
    monkeypatch.setattr(node, 'run', fail)
    result = uefi_service.set_next(approve(plan))
    assert result['state'] == 'UEFI_UNDETERMINED'
    before = deepcopy(calls)
    recovered = uefi_service.status(plan['id'])
    assert calls == before
    assert recovered['state'] == ('UEFI_UNDETERMINED' if mode == 'order_changed' else 'UEFI_FAILED')
    with pytest.raises(core.SafetyError):
        uefi_service.reboot({'id': plan['id'], 'confirmed': True})


def test_changed_identity_before_write_blocks(service):
    snap, calls, _ = service
    plan = uefi_service.prepare(request(snap))
    snap['disks'][0]['serial'] = 'DIFFERENT'
    with pytest.raises(core.SafetyError):
        uefi_service.set_next(approve(plan))
    assert not calls


def test_existing_bootnext_never_overwritten(service):
    snap, calls, _ = service
    snap['boot']['nvram']['next'] = '0002'
    plan = uefi_service.prepare(request(snap))
    assert plan['blocks']
    with pytest.raises(core.SafetyError):
        uefi_service.set_next(approve(plan))
    assert not calls


def start(service):
    snap, calls, directory = service
    plan = uefi_service.prepare(request(snap))
    record = uefi_service.set_next(approve(plan))
    payload = {'id': record['id'], 'confirmed': True, 'host_id': record['host_id'], 'serial_confirmation': record['target']['serial']}
    return uefi_service.reboot(payload), payload


def test_reboot_requires_second_consent_and_is_journaled_before_command(service, monkeypatch):
    record, payload = start(service)
    assert record['state'] == 'UEFI_REBOOT_REQUESTED'
    assert service[1][-1] == ['shutdown', '-r', '+1']
    assert node.load_record(record['id'])['reboot_confirmed']
    with pytest.raises(core.SafetyError):
        uefi_service.reboot(payload)


def test_post_boot_renamed_linux_devices_and_changed_bootorder_pass(service):
    record, _ = start(service)
    snap, calls, _ = service
    snap['boot_id'] = 'NEW-BOOT'
    snap['boot']['nvram'].update(current=record['entry']['id'], next='', order=['0002', '0001'])
    disk = snap['disks'][0]
    disk['path'] = '/dev/sdz'
    for part in disk['partitions']:
        previous = part['path']; part['path'] = '/dev/sdz' + str(part['number'])
        if previous in snap['boot']['esps']:
            snap['boot']['esps'][part['path']] = snap['boot']['esps'].pop(previous)
    before = deepcopy(calls)
    result = uefi_service.status(record['id'])
    assert result['state'] == 'UEFI_PASSED'
    assert result['observation']['boot_order_changed']
    assert result['observation']['binding']['partuuid'] == record['binding']['partuuid']
    assert 'Autonomia fisica non certificata' in result['result_message']
    assert calls == before


@pytest.mark.parametrize('change,state', [
    (lambda s: s['boot']['nvram'].update(current='0002'), 'UEFI_UNCONFIRMED'),
    (lambda s: s['pools'][0].update(state='DEGRADED'), 'UEFI_FAILED'),
    (lambda s: s['boot']['nvram'].update(code=1), 'UEFI_UNDETERMINED'),
    (lambda s: s.update(host_id='OTHER-HOST'), 'UEFI_UNDETERMINED'),
])
def test_postboot_negative_results(service, change, state):
    record, _ = start(service)
    snap = service[0]; snap['boot_id'] = 'NEW-BOOT'; change(snap)
    assert uefi_service.status(record['id'])['state'] == state


def test_app_close_recovery_and_export(service, tmp_path):
    record, _ = start(service)
    store = Store(tmp_path / 'client'); store.save_uefi_test(record)
    recovered = Store(tmp_path / 'client').uefi_tests(record['host_id'])[0]
    assert recovered['id'] == record['id'] and recovered['state'] == 'UEFI_REBOOT_REQUESTED'
    assert store.uefi_tests('OTHER-HOST') == []
    anonymous = json.dumps(core.anonymize({'uefi_tests': [recovered]}))
    for value in (record['host_id'], record['target']['serial'], record['target']['by_id'], record['binding']['partuuid']):
        assert value not in anonymous


def test_no_new_boot_does_not_pass(service):
    record, _ = start(service)
    result = uefi_service.status(record['id'])
    assert result['state'] == 'UEFI_REBOOT_REQUESTED'
    assert result['observation']['state'] == 'UEFI_UNDETERMINED'


def test_readonly_helper_rejects_both_mutations(service, monkeypatch):
    monkeypatch.setattr(node.sys, 'argv', ['helper.pyz', '--read-only'])
    for operation in ('uefi_set_next', 'uefi_reboot'):
        with pytest.raises(core.SafetyError, match='sola lettura'):
            node.dispatch({'op': operation})
    plan = node.dispatch({'op': 'uefi_preview', 'request': request(service[0])})
    assert plan['read_only'] and not service[1]


def test_no_shell_arguments_from_entry_input(service):
    plan = uefi_service.prepare({**request(service[0]), 'entry_id': '0001; reboot'})
    assert plan['blocks'] and not plan['entry']
    assert not service[1]


def test_discovery_resolves_only_existing_links_and_never_fabricates(monkeypatch):
    links = {'/dev/disk/by-id/wwn-shared': '/dev/sda', '/dev/disk/by-id/ata-model-SERIAL_A': '/dev/sda',
             '/dev/disk/by-id/ata-model-SERIAL_B': '/dev/sdc', '/dev/disk/by-id/ata-model-SERIAL_A-part3': '/dev/sda3'}
    monkeypatch.setattr(node.glob, 'glob', lambda pattern: list(links) + ['/dev/disk/by-id/missing'] if pattern == '/dev/disk/by-id/*' else [])
    monkeypatch.setattr(node.os.path, 'islink', lambda p: p in links or p.endswith('missing'))
    monkeypatch.setattr(node.os.path, 'exists', lambda p: p in links)
    monkeypatch.setattr(node.os.path, 'realpath', lambda p: links[p])
    monkeypatch.setattr(node.shutil, 'which', lambda *a, **k: None)
    monkeypatch.setattr(node, 'read_text', lambda p: '3')
    monkeypatch.setattr(node, 'run', lambda argv: {'code': 0, 'out': '{"signatures": []}', 'err': ''})
    blocks = [{'name': '/dev/sda', 'type': 'disk', 'serial': 'SERIAL_A', 'wwn': 'shared', 'size': 1000,
               'children': [{'name': '/dev/sda3', 'type': 'part', 'partuuid': 'synthetic-zfs'}]},
              {'name': '/dev/sdc', 'type': 'disk', 'serial': 'SERIAL_B', 'wwn': 'shared', 'size': 1000}]
    disks = node.discover_disks(blocks)
    assert disks[0]['by_id'] == '/dev/disk/by-id/ata-model-SERIAL_A'
    assert disks[1]['by_id'] == '/dev/disk/by-id/ata-model-SERIAL_B'
    assert disks[0]['partitions'][0]['aliases'] == ['/dev/disk/by-id/ata-model-SERIAL_A-part3']
    assert all(d['identity_verified'] for d in disks)
    assert not any('missing' in a for d in disks for a in d['aliases'])


def test_old_wwn_journal_matches_new_preferred_serial_alias():
    disk = inventory()['disks'][0]
    disk['aliases'].append('/dev/disk/by-id/wwn-5000000000000001')
    old = {**disk, 'by_id': disk['aliases'][-1]}
    assert identity.same_disk_identity(disk, old)
    assert not identity.same_disk_identity({**disk, 'serial': 'OTHER'}, old)


def test_duplicate_alias_is_not_resolved_to_first_disk():
    snapshot = inventory()
    shared = '/dev/disk/by-id/ambiguous'
    for disk in snapshot['disks'][:2]:
        disk['aliases'].append(shared)
    assert core.disk_for(snapshot, shared) is None


def test_uefi_result_cannot_record_physical_autonomy(service):
    record, _ = start(service)
    with pytest.raises(core.SafetyError, match='questa fase'):
        node.record_boot_test({'id': record['id'], 'confirmed': True})


def test_bootnext_journal_saved_before_command(service, monkeypatch):
    plan = uefi_service.prepare(request(service[0]))
    runner = node.run
    def run(argv):
        if argv[:2] == ['efibootmgr', '-n']:
            persisted = node.load_record(plan['id'])
            assert persisted['state'] == 'UEFI_SETTING_NEXT'
            assert persisted['events'][-1]['state'] == 'UNKNOWN'
            assert all(persisted['confirmations'].values())
        return runner(argv)
    monkeypatch.setattr(node, 'run', run)
    assert uefi_service.set_next(approve(plan))['state'] == 'UEFI_READY_REBOOT'


def test_zero_boot_number_is_determined_from_entry_not_hardcoded(service):
    snap, calls, _ = service
    snap['boot']['nvram']['entries'][0]['id'] = '0000'
    snap['boot']['nvram'].update(current='0002', order=['0002', '0000'])
    plan = uefi_service.prepare(request(snap))
    assert plan['entry']['id'] == '0000'
    assert uefi_service.set_next(approve(plan))['state'] == 'UEFI_READY_REBOOT'
    assert calls[0] == ['efibootmgr', '-n', '0000']


def test_pending_bootnext_prevents_a_second_test(service):
    snap, calls, _ = service
    first = uefi_service.prepare(request(snap))
    second = uefi_service.prepare({**request(snap), 'target': snap['disks'][1]['by_id']})
    uefi_service.set_next(approve(first))
    with pytest.raises(core.SafetyError, match='Altro intervento'):
        uefi_service.set_next(approve(second))
    assert len([a for a in calls if a[:2] == ['efibootmgr', '-n']]) == 1


@pytest.mark.parametrize('condition', ['cluster', 'ha', 'running_guest', 'unknown_guests'])
def test_power_preflight_blocks_unsafe_reboot(monkeypatch, condition):
    snapshot = inventory()
    monkeypatch.setattr(node.Path, 'exists', lambda self: condition == 'cluster' and str(self).endswith('corosync.conf'))
    monkeypatch.setattr(node.glob, 'glob', lambda pattern: ['resources.cfg'] if condition == 'ha' else [])
    monkeypatch.setattr(node, 'read_text', lambda p: 'enabled')
    def run(argv):
        if argv[0] == 'pvecm':
            return {'code': 1, 'out': '', 'err': ''}
        return {'code': 1 if condition == 'unknown_guests' else 0,
                'out': json.dumps([{'node': snapshot['hostname'], 'status': 'running'}]) if condition == 'running_guest' else '[]', 'err': ''}
    monkeypatch.setattr(node, 'run', run)
    with pytest.raises(core.SafetyError):
        node.power_preflight(snapshot)
