from copy import deepcopy
import json
import sys

import pytest

from pve_disk_assistant import boot, core, demo, node
from test_core import inventory, boot_inventory, request


def test_grub_zfs_reads_actual_referenced_files(monkeypatch):
    snapshot = inventory()
    text = 'linux /ROOT/pve-1@/boot/vmlinuz-test root=ZFS=rpool/ROOT/pve-1\ninitrd /ROOT/pve-1@/boot/initrd.img-test'
    monkeypatch.setattr(boot.Path, 'is_file', lambda p: True)
    from types import SimpleNamespace
    monkeypatch.setattr(boot.Path, 'stat', lambda p: SimpleNamespace(st_size=100))
    run = lambda args: {'code': 0, 'out': json.dumps({'filesystems': [{'source': 'rpool/ROOT/pve-1', 'fstype': 'zfs'}]})}
    report = boot.inspect_grub_zfs(text, snapshot, run)
    assert report['state'] == 'OK'
    demo.add_boot_observations(snapshot, 'grub')
    snapshot['boot']['grub_zfs'] = report
    for info in snapshot['boot']['esps'].values():
        info['files'] = [f for f in info['files'] if f['path'].lower().endswith('.efi')]
    assert boot.boot_report(snapshot, snapshot['pools'][0])['configuration_ready']
    monkeypatch.setattr(boot.Path, 'is_file', lambda p: not str(p).endswith('initrd.img-test'))
    assert boot.inspect_grub_zfs(text, snapshot, run)['state'] == 'ERROR'


def test_unknown_grub_paths_are_not_executed():
    assert boot.inspect_grub_zfs('linux $prefix/kernel\ninitrd /unknown', inventory(), lambda a: pytest.fail('no command'))['state'] == 'UNKNOWN'


def test_systemd_entry_must_reference_existing_files():
    snapshot = inventory()
    esp = snapshot['boot']['esps']['/dev/sda2']
    entry = next(f for f in esp['files'] if f['path'].endswith('.conf'))
    entry['text'] = 'linux /missing-kernel\ninitrd /missing-initrd'
    report = boot.boot_report(snapshot, snapshot['pools'][0])
    assert report['disks'][0]['checks']['configuration']['state'] == 'ERROR'


@pytest.mark.parametrize('kind,count', [('mirror', 3), ('raidz1', 5), ('raidz2', 6), ('raidz3', 7)])
def test_extended_data_vdevs_keep_existing_group(kind, count):
    snapshot = inventory(True)
    group = snapshot['pools'][1]['children'][0]
    group['kind'] = kind
    while len(group['children']) < count:
        member = deepcopy(group['children'][0])
        disk = deepcopy(core.disk_for_member(snapshot, member))
        suffix = str(len(group['children']))
        disk.update(serial='EXTRA'+suffix, by_id='/dev/disk/by-id/ata-EXTRA'+suffix, path='/dev/extra'+suffix, aliases=[])
        member.update(name=disk['by_id'], guid='extra'+suffix)
        snapshot['disks'].append(disk)
        group['children'].append(member)
    plan = core.plan_replacement(snapshot, request())
    assert not plan['blocks'], plan['blocks']
    assert plan['steps'][-1]['argv'][:2] == ['zpool', 'replace']


def test_survivor_repair_has_no_format_or_gpt_dependency():
    snapshot = boot_inventory(grub=True)
    target = snapshot['disks'][0]
    plan = core.plan_replacement(snapshot, {'action': 'repair_boot', 'pool_guid': '1000', 'target': target['by_id']})
    assert not plan['blocks'], plan['blocks']
    assert [s['argv'][:2] for s in plan['steps']] == [['proxmox-boot-tool', 'init'], ['proxmox-boot-tool', 'refresh'], ['proxmox-boot-tool', 'status']]
    snapshot['boot']['esps']['/dev/sda2']['probe'] = {}
    assert core.plan_replacement(snapshot, plan['request'])['blocks']


def test_uuid_cleanup_requires_healthy_mirror_and_both_esps():
    snapshot = inventory()
    snapshot['boot']['missing_uuids'] = ['DEAD-0000']
    req = {'action': 'clean_boot', 'pool_guid': '1000', 'target': snapshot['disks'][0]['by_id']}
    plan = core.plan_replacement(snapshot, req)
    assert not plan['blocks'], plan['blocks']
    assert plan['steps'][0]['argv'] == ['proxmox-boot-tool', 'clean']
    snapshot['boot']['esps']['/dev/sdb2']['files'] = []
    assert core.plan_replacement(snapshot, req)['blocks']


@pytest.mark.parametrize('operation', ['submit', 'status', 'shutdown', 'identify', 'plan'])
def test_readonly_helper_blocks_even_status_resume(monkeypatch, operation):
    monkeypatch.setattr(sys, 'argv', ['helper.pyz', '--read-only'])
    with pytest.raises(core.SafetyError, match='sola lettura'):
        node.dispatch({'op': operation})


def test_dryrun_does_not_save_or_execute(monkeypatch):
    monkeypatch.setattr(node, 'state_dir', lambda: None)
    monkeypatch.setattr(node, 'collect', inventory)
    monkeypatch.setattr(node, 'save_record', lambda p: pytest.fail('must not persist'))
    result = node.dispatch({'op': 'preview', 'request': request()})
    assert result['read_only'] and result['steps']


def test_all_initrd_directives_are_checked():
    files = {'loader/entries/test.conf': {'text': 'linux /kernel\ninitrd /microcode\ninitrd /missing'},
             'kernel': {'size': 1}, 'microcode': {'size': 1}}
    assert boot.entry_report(files, ['loader/entries/test.conf'], 'systemd')['state'] == 'ERROR'


def test_readonly_transport_never_sends_modifying_rpc(monkeypatch, tmp_path):
    from pve_disk_assistant.transport import SSHBackend, Connection
    backend = SSHBackend(Connection('example.invalid', read_only=True), tmp_path/'known_hosts')
    monkeypatch.setattr(backend, '_call', lambda *args: pytest.fail('must not send'))
    with pytest.raises(RuntimeError, match='sola lettura'):
        backend.rpc({'op': 'submit'})


def test_second_healthy_vdev_is_preserved():
    snapshot = inventory()
    group = deepcopy(snapshot['pools'][1]['children'][0])
    group.update(name='mirror-1', guid='second-vdev', state='ONLINE')
    for i, member in enumerate(group['children']):
        disk = deepcopy(snapshot['disks'][2])
        disk.update(by_id='/dev/disk/by-id/ata-SECOND'+str(i), serial='SECOND'+str(i), path='/dev/second'+str(i), aliases=[])
        member.update(name=disk['by_id'], guid='second'+str(i), state='ONLINE', errors=[0,0,0])
        snapshot['disks'].append(disk)
    snapshot['pools'][1]['children'].append(group)
    plan = core.plan_replacement(snapshot, request())
    assert not plan['blocks'], plan['blocks']
    assert plan['old_guid'] == '202'
    group['children'][0]['state'] = 'FAULTED'
    assert core.plan_replacement(snapshot, request())['blocks']
