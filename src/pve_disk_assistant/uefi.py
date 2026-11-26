"""Pure preflight and post-boot evaluation. A UEFI test is not physical autonomy."""
import re
from copy import deepcopy

from .boot import boot_report, normalize_path
from .core import VERSION_BLOCK, lab_version_supported, SafetyError, GOOD, data_groups, digest, disk_for_member, leaves
from .identity import normalize_wwn, physical_identity_verified

PENDING = {'UEFI_SETTING_NEXT', 'UEFI_READY_REBOOT', 'UEFI_REBOOT_REQUESTED', 'UEFI_UNDETERMINED'}
LABELS = {
    'UEFI_PREVIEW': 'Test UEFI non eseguito', 'UEFI_SETTING_NEXT': 'Impostazione BootNext da verificare',
    'UEFI_READY_REBOOT': 'BootNext verificato · riavvio da approvare',
    'UEFI_REBOOT_REQUESTED': 'Riavvio richiesto · verifica in attesa',
    'UEFI_PASSED': 'Test UEFI superato', 'UEFI_UNCONFIRMED': 'Test UEFI non confermato',
    'UEFI_FAILED': 'Test UEFI fallito', 'UEFI_UNDETERMINED': 'Esito non determinato',
}
CONFIRMATIONS = ('backup', 'maintenance', 'console', 'recovery')


def healthy_pool(snapshot, pool):
    groups = data_groups(pool)
    members = leaves(pool)
    return bool(snapshot.get('complete') and not snapshot.get('issues') and not pool.get('warnings')
                and len(groups) == 1 and groups[0]['kind'] == 'mirror' and len(members) == 2
                and all(c.get('section') == 'data' for c in pool['children'])
                and pool['state'] == 'ONLINE' and pool.get('errors') == 'No known data errors'
                and 'in progress' not in pool.get('scan', '').lower()
                and all(m['state'] in GOOD and len(m.get('errors', [])) == 3 and not any(m['errors'])
                        and disk_for_member(snapshot, m) for m in members))


def preflight(snapshot, request):
    """Select exactly one existing entry; never create or guess firmware paths."""
    blocks = []
    pool = next((p for p in snapshot.get('pools', []) if p['guid'] == request.get('pool_guid')), None)
    target = next((d for d in snapshot.get('disks', []) if d.get('by_id') == request.get('target')), None)
    if not pool or not target:
        raise SafetyError('Pool o disco selezionato non più presente.')
    if not physical_identity_verified(snapshot['disks'], target):
        blocks.append('Identità fisica ambigua o Disk by ID non verificato.')
    if not lab_version_supported(snapshot):
        blocks.append(VERSION_BLOCK)
    if target.get('table', {}).get('label') != 'gpt':
        blocks.append('Tabella GPT del disco selezionato non verificata.')
    root_pool = snapshot.get('root', {}).get('source', '').split('/')[0]
    if snapshot.get('root', {}).get('fstype') != 'zfs' or pool['name'] != root_pool:
        blocks.append('Il disco non appartiene al pool ZFS del sistema root.')
    members = [m for m in leaves(pool) if disk_for_member(snapshot, m) == target]
    if len(members) != 1 or not members[0].get('guid'):
        blocks.append('Associazione disco / GUID ZFS non univoca.')
    if not healthy_pool(snapshot, pool):
        blocks.append('Il test richiede un mirror di sistema di due membri ONLINE, senza errori o ricostruzioni in corso.')
    boot = snapshot.get('boot', {})
    nvram = boot.get('nvram', {})
    if boot.get('firmware_mode') != 'UEFI' or boot.get('architecture') != 'x86_64':
        blocks.append('Modalità UEFI x86_64 non verificata; layout non supportato.')
    if (nvram.get('code') != 0 or nvram.get('ambiguous') or
            not re.fullmatch(r'[0-9A-F]{4}', nvram.get('current', '')) or not nvram.get('order')):
        blocks.append('Variabili firmware non leggibili o ambigue.')
    if 'efibootmgr' not in snapshot.get('tools', []):
        blocks.append('efibootmgr non disponibile.')
    if not snapshot.get('boot_id') or not snapshot.get('host_id') or not snapshot.get('kernel', '').endswith('-pve'):
        blocks.append('Identità del nodo, avvio o kernel Proxmox non verificati.')
    report = boot_report(snapshot, pool)
    row = next((r for r in report['disks'] if r.get('by_id') == target['by_id']), {})
    esp = row.get('esp') or {}
    if not report['configuration_ready']:
        blocks.append('Configurazione EFI di entrambi i membri da verificare prima del test.')
    partuuid = esp.get('partuuid', '').lower()
    if not partuuid or sum(p.get('partuuid', '').lower() == partuuid for d in snapshot['disks'] for p in d.get('partitions', [])) != 1:
        blocks.append('PARTUUID della ESP assente o duplicato.')
    files = {normalize_path(f['path']): f for f in boot.get('esps', {}).get(esp.get('path'), {}).get('files', [])}
    candidates = []
    for entry in nvram.get('entries', []):
        file = files.get(entry.get('file'), {})
        kind = file.get('loader_kind')
        supported = (row.get('mode') == 'grub' and kind in {'grub', 'shim'} or row.get('mode') == 'systemd' and kind == 'systemd')
        if (entry.get('partuuid', '').lower() == partuuid and partuuid and entry.get('active')
                and re.fullmatch(r'[0-9A-F]{4}', entry.get('id', '')) and file.get('efi_valid') and supported):
            candidates.append(entry)
    requested = request.get('entry_id')
    choices = [e for e in candidates if not requested or e['id'] == requested]
    entry = choices[0] if len(choices) == 1 else {}
    if not entry:
        blocks.append('Selezionare una voce UEFI univoca con PARTUUID corretto e file EFI presente e riconosciuto.')
    binding = {'host_id': snapshot.get('host_id'), 'pool_guid': pool['guid'],
               'member_guid': members[0]['guid'] if len(members) == 1 else '',
               'by_id': target.get('by_id'), 'serial': target.get('serial'), 'model': target.get('model'),
               'wwn': normalize_wwn(target.get('wwn')), 'size': target.get('size'),
               'partuuid': partuuid, 'esp_uuid': esp.get('uuid', '').upper(), 'mode': row.get('mode'),
               'entry': deepcopy(entry)}
    return {'action': 'uefi_test', 'host_id': snapshot.get('host_id'), 'hostname': snapshot.get('hostname'),
            'pool': pool['name'], 'pool_guid': pool['guid'], 'role': pool['role'], 'raid': 'MIRROR',
            'target': deepcopy(target), 'expected_esp': deepcopy(esp), 'entry': deepcopy(entry),
            'candidates': candidates, 'binding': binding, 'evidence_digest': row.get('evidence_digest'),
            'boot_id_before': snapshot.get('boot_id'), 'boot_before': deepcopy(nvram),
            'kernel_before': snapshot.get('kernel'), 'boot_validation': report, 'blocks': blocks,
            'request': {k: request[k] for k in ('pool_guid', 'target', 'entry_id', 'replacement_id') if k in request},
            'replacement_id': request.get('replacement_id'),
            'warnings': ['Il test UEFI non certifica l’avvio con un solo disco. Nessuna modifica a BootOrder.',
                         *target.get('identity_warnings', [])],
            'steps': [{'command': 'efibootmgr -n ' + entry.get('id', 'NON_VERIFICATA'),
                       'explanation': 'Seleziona la voce per il solo prossimo avvio; poi rilegge BootNext e BootOrder.'},
                      {'command': 'shutdown -r +1', 'explanation': 'Solo dopo una SECONDA approvazione: riavvio fra un minuto, con controlli sui carichi e sul cluster.'}]}


def post_boot(record, snapshot):
    """Return evidence without changing the physical autonomy journal."""
    result = {'checked_at': snapshot.get('collected_at'), 'host_id': snapshot.get('host_id'),
              'boot_id': snapshot.get('boot_id'), 'kernel': snapshot.get('kernel'),
              'nvram': deepcopy(snapshot.get('boot', {}).get('nvram', {}))}
    if snapshot.get('host_id') != record['host_id']:
        return {**result, 'state': 'UEFI_UNDETERMINED', 'detail': 'Identità del nodo diversa: esito non determinato.'}
    if not snapshot.get('boot_id') or snapshot['boot_id'] == record['boot_id_before']:
        return {**result, 'state': 'UEFI_UNDETERMINED', 'detail': 'Non è ancora confermato un nuovo avvio.'}
    result['boot_order_changed'] = result['nvram'].get('order') != record['boot_before'].get('order')
    try:
        fresh = preflight(snapshot, record['request'])
    except SafetyError as exc:
        return {**result, 'state': 'UEFI_UNCONFIRMED', 'detail': str(exc)}
    result.update(pool=deepcopy(next(p for p in snapshot['pools'] if p['guid'] == record['pool_guid'])),
                  boot_validation=fresh['boot_validation'], binding=fresh['binding'], blocks=fresh['blocks'])
    nvram = result['nvram']
    if nvram.get('code') != 0 or nvram.get('ambiguous') or not nvram.get('current'):
        return {**result, 'state': 'UEFI_UNDETERMINED', 'detail': 'BootCurrent non verificabile.'}
    if nvram['current'] != record['entry']['id'] or fresh['binding'] != record['binding']:
        return {**result, 'state': 'UEFI_UNCONFIRMED', 'detail': 'Avvio tramite altra voce o associazione fisica/ESP cambiata.'}
    if fresh['blocks']:
        return {**result, 'state': 'UEFI_FAILED', 'detail': 'Voce richiesta utilizzata, ma verifiche ZFS/EFI/kernel non superate: ' + '; '.join(fresh['blocks'])}
    return {**result, 'state': 'UEFI_PASSED', 'detail': 'Nuovo avvio, BootCurrent, PARTUUID, kernel e mirror verificati. Autonomia fisica non certificata.'}
