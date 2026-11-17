"""Node-side UEFI test journal. Writes are single-shot and separately approved."""
import json
import time
import uuid
from copy import deepcopy

from . import __version__
from .boot import parse_boot_entries
from .core import SafetyError, digest
from .uefi import CONFIRMATIONS, PENDING, post_boot, preflight


def api():
    from . import node
    return node


def no_active_operation(identifier=None):
    node = api()
    for path in node.state_dir().glob('*.json'):
        row = json.loads(path.read_text())
        if row['id'] != identifier and row.get('state') in PENDING | {'QUEUED', 'RUNNING', 'WAITING_RESILVER', 'MONITORING', 'UNKNOWN'}:
            raise SafetyError('Altro intervento attivo o test UEFI in sospeso: recuperare prima il suo esito.')


def prepare(request, read_only=False):
    node = api()
    snapshot = node.collect()
    plan = preflight(snapshot, request)
    if plan['boot_before'].get('next'):
        plan['blocks'].append('BootNext è già impostato: non viene sovrascritto.')
    if plan.get('replacement_id'):
        parent = node.load_record(plan['replacement_id'])
        target = parent.get('target', {})
        if (parent.get('action') != 'replace' or parent.get('host_id') != plan['host_id']
                or parent.get('pool_guid') != plan['pool_guid'] or target.get('serial') != plan['target']['serial']
                or target.get('by_id') not in [plan['target']['by_id'], *plan['target'].get('aliases', [])]):
            plan['blocks'].append('Intervento di sostituzione non associabile a questo disco.')
    if plan['entry']:
        plan['request']['entry_id'] = plan['entry']['id']
    plan.update(id=uuid.uuid4().hex, state='UEFI_PREVIEW', created_at=time.time(), events=[],
                helper_version=__version__, read_only=read_only)
    plan['approval_digest'] = digest(plan)
    if not read_only:
        node.save_record(plan)
    return plan


def get_test(identifier):
    row = api().load_record(identifier)
    if row.get('action') != 'uefi_test':
        raise SafetyError('Il registro selezionato non è un test UEFI.')
    return row


def fresh_test(record):
    snapshot = api().collect()
    fresh = preflight(snapshot, record['request'])
    if (fresh['blocks'] or fresh['binding'] != record['binding'] or fresh['boot_id_before'] != record['boot_id_before']
            or fresh['evidence_digest'] != record['evidence_digest']):
        raise SafetyError('Identità, avvio o diagnostica cambiati: nessuna operazione. ' + '; '.join(fresh['blocks']))
    api().verify_target(fresh)
    return snapshot, fresh


def set_next(request):
    node = api()
    with node.locked():
        record = get_test(request.get('id'))
        if record['state'] != 'UEFI_PREVIEW' or record.get('read_only'):
            raise SafetyError('Test già inviato o in sola lettura: non ripetere BootNext.')
        if record.get('helper_version') != __version__ or time.time() - record['created_at'] > 600:
            raise SafetyError('Piano scaduto o helper cambiato: ripetere la diagnostica.')
        confirmations = request.get('confirmations', {})
        if (record['blocks'] or request.get('approval_digest') != record['approval_digest'] or
                request.get('serial_confirmation') != record['target']['serial'] or
                not all(confirmations.get(k) is True for k in CONFIRMATIONS)):
            raise SafetyError('Prerequisiti o conferme backup/manutenzione/console/recupero mancanti.')
        no_active_operation(record['id'])
        _, fresh = fresh_test(record)
        if fresh['boot_before'].get('next') or fresh['boot_before']['order'] != record['boot_before']['order']:
            raise SafetyError('BootNext o BootOrder cambiati dopo la diagnostica: nessuna sovrascrittura.')
        record.update(state='UEFI_SETTING_NEXT', approved_at=node.utcnow(), confirmations={k: True for k in CONFIRMATIONS})
        argv = ['efibootmgr', '-n', record['entry']['id']]
        event = {'command': ' '.join(argv), 'state': 'UNKNOWN', 'at': node.utcnow()}
        record['events'].append(event)
        node.save_record(record)  # Persist intent before the firmware write.
        try:
            result = node.run(argv)
            event.update(result, state='OK' if result['code'] == 0 else 'FAILED')
            observed = node.run(['efibootmgr', '-v'])
            nvram = {**parse_boot_entries(observed['out']), 'code': observed['code']}
            record['boot_after_setting'] = nvram
            record['events'].append({'command': 'efibootmgr -v', 'state': 'OK' if observed['code'] == 0 else 'FAILED', **observed})
            if (result['code'] != 0 or observed['code'] != 0 or nvram.get('ambiguous') or
                    nvram.get('next') != record['entry']['id'] or nvram['order'] != record['boot_before']['order']):
                # A nonzero exit can still have written NVRAM. Keep it unresolved until a read-only recovery.
                record.update(state='UEFI_UNDETERMINED', error='BootNext non confermato o BootOrder variato; nessun riavvio o ripristino automatico.')
            else:
                record['state'] = 'UEFI_READY_REBOOT'
        except Exception as exc:
            record.update(state='UEFI_UNDETERMINED', error=str(exc))
        node.save_record(record)
        return record


def reboot(request):
    node = api()
    with node.locked():
        record = get_test(request.get('id'))
        if (record['state'] != 'UEFI_READY_REBOOT' or record.get('helper_version') != __version__ or request.get('confirmed') is not True or
                request.get('host_id') != record['host_id'] or request.get('serial_confirmation') != record['target']['serial']):
            raise SafetyError('Seconda conferma del riavvio, nodo o seriale mancanti; nessun riavvio.')
        no_active_operation(record['id'])
        snapshot, fresh = fresh_test(record)
        if (fresh['boot_before'].get('next') != record['entry']['id'] or
                fresh['boot_before']['order'] != record['boot_before']['order']):
            raise SafetyError('BootNext o BootOrder cambiati: riavvio bloccato.')
        node.power_preflight(snapshot)
        record.update(state='UEFI_REBOOT_REQUESTED', reboot_requested_at=node.utcnow(), reboot_confirmed=True)
        event = {'command': 'shutdown -r +1', 'state': 'UNKNOWN', 'at': node.utcnow()}
        record['events'].append(event)
        node.save_record(record)
        try:
            result = node.run(['shutdown', '-r', '+1'])
            event.update(result, state='OK' if result['code'] == 0 else 'FAILED')
            if result['code']:
                record.update(state='UEFI_UNDETERMINED', error='Riavvio non confermato: verificare tramite console, nessun retry automatico.')
        except Exception as exc:
            record.update(state='UEFI_UNDETERMINED', error=str(exc))
        node.save_record(record)
        return record


def status(identifier):
    node = api()
    node.record_path(identifier)
    with node.locked():
        record = get_test(identifier)
        if record['state'] not in PENDING:
            return record
        try:
            snapshot = node.collect()
            observation = post_boot(record, snapshot)
            record['observation'] = observation
            if snapshot.get('boot_id') == record['boot_id_before'] and snapshot.get('host_id') == record['host_id']:
                if not record.get('reboot_requested_at'):
                    nvram = snapshot.get('boot', {}).get('nvram', {})
                    if nvram.get('code') == 0 and not nvram.get('ambiguous') and nvram.get('order') == record['boot_before']['order']:
                        if nvram.get('next') == record['entry']['id']:
                            record['state'] = 'UEFI_READY_REBOOT'
                        elif not nvram.get('next'):
                            record.update(state='UEFI_FAILED', error='BootNext non impostato o rimosso. Nessuna ripetizione automatica.')
                        else:
                            record['state'] = 'UEFI_UNDETERMINED'
                    else:
                        record['state'] = 'UEFI_UNDETERMINED'
                # A requested reboot may take minutes; no repeated shutdown calls.
            else:
                record['state'] = observation['state']
            record['result_message'] = observation['detail']
        except Exception as exc:
            record.update(state='UEFI_UNDETERMINED', error=str(exc))
        node.save_record(record)
        return record
