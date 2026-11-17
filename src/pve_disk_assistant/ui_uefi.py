"""UEFI test UI on the existing SSH backend. Automatic recovery is read-only."""
import time
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QHBoxLayout,
                              QInputDialog, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                              QTableWidgetItem, QTextBrowser, QVBoxLayout)

from .presentation import job_html, plan_html
from .transport import SSHBackend
from .uefi import LABELS, PENDING, preflight


class UefiMixin:
    def build_uefi_controls(self, layout):
        actions = QHBoxLayout()
        self.uefi_diagnose_button = self.button('Verifica avviabilità', lambda: self.preview_uefi(False), actions)
        self.uefi_test_button = self.button('Prova avvio da questo disco', lambda: self.preview_uefi(True), actions)
        self.button('Verifica post-riavvio / recupera test', lambda: self.refresh_uefi(False), actions)
        self.button('Esporta report', self.export_report, actions, network=False)
        layout.addLayout(actions)
        self.uefi_history = QTableWidget(0, 4)
        self.uefi_history.setHorizontalHeaderLabels(['Data', 'Disk by ID / seriale', 'Voce UEFI', 'Esito del test'])
        self.uefi_history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.uefi_history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.uefi_history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.uefi_history.setMaximumHeight(110)
        self.uefi_history.itemSelectionChanged.connect(self.select_uefi_test)
        layout.addWidget(self.uefi_history)
        actions = QHBoxLayout()
        self.uefi_reboot_button = self.button('Approva riavvio per il test selezionato', self.reboot_uefi, actions)
        self.button('Dettagli / storico test', self.show_uefi_test, actions, network=False)
        self.button('Copia Disk by ID', lambda: self.copy_disk_id('efi'), actions, network=False)
        layout.addLayout(actions)
        self.uefi_records = []
        self.uefi_last_attempt = 0

    def writable_uefi(self):
        return bool(self.backend and not self.is_demo() and not
                    (isinstance(self.backend, SSHBackend) and self.backend.connection.read_only))

    def selected_uefi(self):
        index = self.uefi_history.currentRow()
        return self.uefi_records[index] if 0 <= index < len(self.uefi_records) else None

    def select_uefi_test(self):
        if self.busy:
            return
        record = self.selected_uefi()
        self.uefi_reboot_button.setEnabled(bool(record and record['state'] == 'UEFI_READY_REBOOT' and self.writable_uefi()))

    def render_uefi_history(self):
        if not self.snapshot:
            return
        selected = (self.selected_uefi() or {}).get('id')
        self.uefi_records = list(reversed(self.store.uefi_tests(self.snapshot['host_id'])))
        self.uefi_history.blockSignals(True)
        self.uefi_history.setRowCount(len(self.uefi_records))
        for index, row in enumerate(self.uefi_records):
            stamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(row.get('created_at', 0)))
            values = [stamp, row['target'].get('by_id', '') + ' / ' + row['target']['serial'],
                      row.get('entry', {}).get('id', ''), LABELS.get(row['state'], row['state'])]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.uefi_history.setItem(index, column, item)
        self.uefi_history.resizeColumnsToContents()
        self.uefi_history.blockSignals(False)
        chosen = next((i for i, r in enumerate(self.uefi_records) if r['id'] == selected), 0 if self.uefi_records else -1)
        self.uefi_history.setCurrentCell(chosen, 0)
        self.select_uefi_test()

    def save_uefi(self, record):
        self.store.save_uefi_test(record)
        self.render_uefi_history()
        jobs = [j for j in reversed(self.jobs) if j['id'] != record['id']] + [record]
        self.render_jobs(jobs)
        if self.snapshot:
            selected = self.boot_rows[self.boot_table.currentRow()]['disk'].get('by_id') if 0 <= self.boot_table.currentRow() < len(self.boot_rows) else None
            self.render_boot()
            self.boot_table.setCurrentCell(next((i for i, r in enumerate(self.boot_rows) if r['disk'].get('by_id') == selected), -1), 0)

    def preview_uefi(self, approve=False):
        index = self.boot_table.currentRow()
        if not self.snapshot or index < 0 or index >= len(self.boot_rows):
            return
        selected = self.boot_rows[index]
        request = {'pool_guid': selected['pool']['guid'], 'target': selected['disk']['by_id']}
        candidates = preflight(self.snapshot, request)['candidates']
        if len(candidates) > 1:
            choices = [e['id'] + ' · ' + e['file'] for e in candidates]
            choice, ok = QInputDialog.getItem(self, 'Voce UEFI', 'Più voci verificate per questa ESP: seleziona il percorso da provare.', choices, editable=False)
            if not ok:
                return
            request['entry_id'] = candidates[choices.index(choice)]['id']
        parents = [j for j in self.jobs if j.get('action') == 'replace' and j.get('pool_guid') == request['pool_guid']
                   and j.get('target', {}).get('serial') == selected['disk']['serial']]
        if len(parents) == 1:
            request['replacement_id'] = parents[0]['id']
        operation = 'uefi_plan' if approve and self.writable_uefi() else 'uefi_preview'
        self.asynchronous(lambda: self.backend.rpc({'op': operation, 'request': request}),
                          lambda p: self.uefi_plan_dialog(p, approve and self.writable_uefi()), 'Diagnostica UEFI in sola lettura…')

    def uefi_plan_dialog(self, plan, approve):
        # The callback runs before the worker finishes: only collect consent here.
        dialog = QDialog(self)
        dialog.setWindowTitle('Test UEFI · diagnostica e approvazione BootNext')
        dialog.resize(900, 720)
        layout = QVBoxLayout(dialog)
        detail = QTextBrowser()
        display = {**plan, 'fingerprint': plan.get('evidence_digest', '')}
        detail.setHtml(plan_html(display, plan.get('hostname', '?')))
        layout.addWidget(detail, 1)
        confirmations = {}
        for key, label in [('backup', 'Backup verificato'), ('maintenance', 'Finestra di manutenzione concordata'),
                           ('console', 'Console fisica o KVM disponibile'), ('recovery', 'Possibilità di intervento e recupero in caso di mancato avvio')]:
            checkbox = QCheckBox(label)
            layout.addWidget(checkbox)
            confirmations[key] = checkbox
        serial = QLineEdit()
        serial.setPlaceholderText('Digita il seriale completo del disco per confermare')
        layout.addWidget(serial)
        buttons = QHBoxLayout()
        commit = QPushButton('Approva solo BootNext · nessun riavvio')
        commit.setEnabled(False)
        def validate():
            commit.setEnabled(bool(approve and not plan['blocks'] and not plan.get('read_only') and
                                   all(c.isChecked() for c in confirmations.values()) and serial.text() == plan['target']['serial']))
        for checkbox in confirmations.values():
            checkbox.toggled.connect(validate)
        serial.textChanged.connect(validate)
        commit.clicked.connect(dialog.accept)
        close = QPushButton('Chiudi')
        close.clicked.connect(dialog.reject)
        buttons.addWidget(commit); buttons.addWidget(close); layout.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.save_uefi_test(plan)
        request = {'op': 'uefi_set_next', 'id': plan['id'], 'approval_digest': plan['approval_digest'],
                   'serial_confirmation': serial.text(), 'confirmations': {k: c.isChecked() for k, c in confirmations.items()}}
        self.queue_uefi_write(request, plan, 'UEFI_SETTING_NEXT')

    def queue_uefi_write(self, request, record, intent):
        from PySide6.QtCore import QTimer
        # Delay only until the read worker releases the UI; never retry the write.
        backend = self.backend
        def start():
            if self.backend is not backend:
                return
            if self.busy:
                QTimer.singleShot(100, start)
                return
            pending = deepcopy(record)
            pending.update(state=intent, local_intent_at=time.time())
            self.save_uefi(pending)  # SQLite is durable before sending either write.
            def send():
                try:
                    return backend.rpc(request)
                except Exception as exc:
                    return {**pending, 'state': 'UEFI_UNDETERMINED', 'connection_error': str(exc)}
            self.asynchronous(send, self.save_uefi, 'Invio singolo comando approvato; nessun retry…', retry=False)
        QTimer.singleShot(0, start)

    def reboot_uefi(self):
        record = self.selected_uefi()
        if not record or record['state'] != 'UEFI_READY_REBOOT' or not self.writable_uefi():
            return
        message = ('Nodo: ' + record['hostname'] + '\nDisco: ' + record['target']['serial'] + '\n' + record['target']['by_id'] +
                   '\nVoce: Boot' + record['entry']['id'] + '\n\nComando: shutdown -r +1\nRiavvio fra un minuto. SSH verrà interrotto. '
                   'Ferma prima VM/CT. Cluster e HA richiedono manutenzione esterna. Confermi console, recupero e RIAVVIO?')
        if QMessageBox.question(self, 'Seconda approvazione · riavvio', message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.queue_uefi_write({'op': 'uefi_reboot', 'id': record['id'], 'confirmed': True, 'host_id': record['host_id'],
                              'serial_confirmation': record['target']['serial']}, record, 'UEFI_REBOOT_REQUESTED')

    def refresh_uefi(self, automatic=False):
        if self.busy or not self.backend or not self.snapshot:
            return
        host_id, backend = self.snapshot['host_id'], self.backend
        pending = [r for r in self.store.uefi_tests(host_id) if r['state'] in PENDING]
        self.uefi_last_attempt = time.monotonic()
        def read():
            try:
                snapshot = backend.rpc({'op': 'collect'})
                if snapshot['host_id'] != host_id:
                    raise RuntimeError('Identità nodo cambiata: riconnessione non confermata.')
                records = backend.rpc({'op': 'list_jobs'})
                records = [r for r in records if r.get('action') == 'uefi_test']
                if not (isinstance(backend, SSHBackend) and backend.connection.read_only):
                    records = [backend.rpc({'op': 'status', 'id': r['id']}) if r['state'] in PENDING else r for r in records]
                    listed = {r['id'] for r in records}
                    records += [backend.rpc({'op': 'status', 'id': r['id']}) for r in pending if r['id'] not in listed]
                return {'snapshot': snapshot, 'records': records}
            except Exception as exc:
                return {'error': str(exc)}
        def received(result):
            if result.get('error'):
                for row in pending:
                    self.store.save_uefi_test({**row, 'state': 'UEFI_UNDETERMINED', 'connection_error': result['error']})
                self.render_uefi_history()
                self.statusBar().showMessage('Esito UEFI non determinato · ' + result['error'].splitlines()[0])
                if not automatic:
                    QMessageBox.information(self, 'Verifica UEFI', result['error'] + '\nNessun comando modificativo ripetuto.')
                return
            for row in result['records']:
                self.store.save_uefi_test(row)
            self.render_snapshot(result['snapshot'])
            others = [j for j in reversed(self.jobs) if j.get('action') != 'uefi_test']
            self.render_jobs(others + self.store.uefi_tests(host_id))
        self.asynchronous(read, received, 'Riconnessione e verifica UEFI in sola lettura…', retry=False)

    def show_uefi_test(self):
        record = self.selected_uefi()
        if not record:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Storico test UEFI · separato dall’autonomia fisica')
        dialog.resize(900, 650)
        layout = QVBoxLayout(dialog)
        detail = QTextBrowser(); detail.setHtml(job_html(record)); layout.addWidget(detail)
        close = QPushButton('Chiudi'); close.clicked.connect(dialog.accept); layout.addWidget(close)
        dialog.exec()

    def copy_disk_id(self, section):
        disk = None
        if section == 'efi' and 0 <= self.boot_table.currentRow() < len(self.boot_rows):
            key = self.boot_rows[self.boot_table.currentRow()]['disk'].get('by_id')
            disk = next((d for d in self.snapshot['disks'] if d.get('by_id') == key), None)
        elif section == 'diagnosis':
            disk = self.selected_disk()
        elif section == 'replacement':
            disk = (self.plan or {}).get('target')
        elif section == 'jobs' and 0 <= self.job_table.currentRow() < len(self.jobs):
            disk = self.jobs[self.job_table.currentRow()].get('target')
        elif section == 'overview' and self.tree.currentItem():
            from .core import leaves, disk_for_member
            pool_id, member_id = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole)
            pool = next(p for p in self.snapshot['pools'] if p['guid'] == pool_id)
            member = next((m for m in leaves(pool) if m.get('guid') == member_id), None)
            disk = disk_for_member(self.snapshot, member) if member else None
        if disk and disk.get('identity_verified') and disk.get('by_id'):
            QApplication.clipboard().setText(disk['by_id'])
            self.statusBar().showMessage('Disk by ID completo copiato')
        else:
            self.statusBar().showMessage('Seleziona un disco con Disk by ID verificato')
