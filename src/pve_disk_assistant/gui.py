"""Italian desktop UI. Every SSH task runs outside the Qt UI thread."""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QTextDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QScrollArea, QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QProgressBar)

from . import __version__
from .boot import boot_report
from .core import (BAD, GOOD, anonymize, safety_fingerprint, data_groups, disk_for, disk_for_member, leaves,
                   raid_label, command_text)
from .demo import DemoBackend
from .store import Store
from .transport import Connection, HostVerificationRequired, SSHBackend

STYLE = """
QWidget { background:#0e1523; color:#d9e4f5; font-family:'Segoe UI'; font-size:10pt; }
QMainWindow { background:#0e1523; }
QLabel#title { font-size:24pt; font-weight:700; color:#f4f8ff; }
QLabel#subtitle { color:#96a9c6; font-size:11pt; }
QLabel#badge { color:#6fdec5; background:#152b35; padding:7px 12px; border-radius:8px; }
QFrame#card { background:#172235; border:1px solid #26354d; border-radius:12px; }
QFrame#card QLabel { background:transparent; }
QLabel#metric { font-size:22pt; font-weight:700; }
QLabel#muted { color:#96a9c6; }
QTabWidget::pane { border:1px solid #26354d; border-radius:8px; }
QTabBar::tab { background:#172235; padding:12px 20px; margin-right:4px; }
QTabBar::tab:selected { background:#253b51; color:#7ce4ce; }
QGroupBox { border:1px solid #2c3d56; border-radius:8px; margin-top:12px; padding:15px; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 6px; color:#b5c8e3; }
QPushButton { background:#263b53; border:1px solid #3b526f; border-radius:6px; padding:8px 13px; }
QPushButton:hover { background:#34516e; }
QPushButton:disabled { color:#64758c; background:#172235; border-color:#26354d; }
QPushButton#primary { background:#177c6a; color:white; border-color:#269780; font-weight:600; }
QPushButton#danger { background:#753d47; border-color:#a25a65; }
QLineEdit, QSpinBox, QComboBox { background:#111d2e; border:1px solid #34465f; border-radius:5px; padding:7px; }
QTreeWidget, QTableWidget, QPlainTextEdit, QTextBrowser { background:#111c2c; border:1px solid #293a52; border-radius:5px; }
QTreeWidget::item, QTableWidget::item { padding:7px; }
QTreeWidget::item:selected, QTableWidget::item:selected { background:#244a60; }
QHeaderView::section { background:#1e2d43; color:#a8bdd9; padding:8px; border:0; }
QTextBrowser { padding:10px; }
QCheckBox { spacing:8px; }
QStatusBar { background:#172235; }
QSplitter::handle { background:#26354d; width:5px; }
"""


def size_text(size):
    return f"{size / 1_000_000_000:,.1f} GB" if size else "—"


def color_for(state):
    return QColor("#71d7b3" if state in GOOD | {"COMPLETED", "OK"} else
                  "#f4bf65" if state in {"DEGRADED", "MONITORING", "RUNNING", "QUEUED", "WAITING_RESILVER", "BOOT_TEST_REQUIRED"} else
                  "#f18c99" if state in BAD | {"FAILED", "UNKNOWN", "BLOCKED", "ERROR", "BOOT_REVIEW_REQUIRED", "ZFS_REVIEW_REQUIRED"} else "#b9cce6")


class Worker(QThread):
    result = Signal(object)
    error = Signal(object)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function())
        except Exception as exc:
            self.error.emit(exc)


class Window(QMainWindow):
    def __init__(self, directory, demo=True):
        super().__init__()
        self.setWindowTitle("Proxmox ZFS Assistant")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1380, 940)
        self.store = Store(directory)
        self.backend = DemoBackend() if demo else None
        self.snapshot = None
        self.plan = None
        self.busy = False
        self.worker = None
        self.jobs = []
        self.boot_rows = []
        self.last_error = ""
        self.raid_questions = set()
        self.network_buttons = []
        self._building = False
        self._build()
        self.poll = QTimer(self)
        self.poll.setInterval(20000)
        self.poll.timeout.connect(self._poll)
        self.poll.start()
        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self._age)
        self.age_timer.start(1000)
        if demo:
            QTimer.singleShot(0, self.scan)

    def button(self, title, callback, parent_layout, name="", network=True):
        button = QPushButton(title)
        if name:
            button.setObjectName(name)
        button.clicked.connect(callback)
        parent_layout.addWidget(button)
        if network:
            self.network_buttons.append(button)
        return button

    def _build(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 16)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Proxmox ZFS Assistant")
        title.setObjectName("title")
        subtitle = QLabel("Diagnosi dei dischi  /  Topologia ZFS  /  Sostituzione guidata")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.badge = QLabel("DEMO · nessun server collegato")
        self.badge.setObjectName("badge")
        header.addWidget(self.badge)
        layout.addLayout(header)
        self.freshness = QLabel("Pronto. Carica la demo oppure configura una connessione SSH.")
        self.freshness.setObjectName("muted")
        layout.addWidget(self.freshness)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._overview()
        self._disks()
        self._replacement()
        self._jobs()
        self._connection()
        self._boot_diagnostics()
        self.setCentralWidget(central)
        self.statusBar().showMessage("Versione " + __version__ + " · progetto indipendente da Proxmox")

    def _overview(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        actions = QHBoxLayout()
        self.button("Aggiorna analisi", self.scan, actions, "primary")
        self.live_refresh = QCheckBox("Aggiornamento automatico")
        actions.addWidget(self.live_refresh)
        actions.addStretch()
        self.demo_raid = QComboBox()
        self.demo_raid.addItems(["Demo · VM mirror", "Demo · VM RAIDZ1", "Demo · EFI non valida"])
        actions.addWidget(self.demo_raid)
        self.button("Carica demo", self.load_demo, actions)
        self.button("Esporta rapporto anonimizzato", self.export_report, actions, network=False)
        layout.addLayout(actions)
        cards = QHBoxLayout()
        self.card_values = {}
        for label, key in (("POOL RILEVATI", "pools"), ("DISCHI PRESENTI", "disks"),
                           ("MEMBRI CON ANOMALIE", "bad"), ("RUOLO ROOT", "root"), ("CONFIGURAZIONE EFI", "boot")):
            frame = QFrame()
            frame.setObjectName("card")
            box = QVBoxLayout(frame)
            small, large = QLabel(label), QLabel("—")
            small.setObjectName("muted")
            large.setObjectName("metric")
            box.addWidget(small)
            box.addWidget(large)
            cards.addWidget(frame)
            self.card_values[key] = large
        layout.addLayout(cards)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Pool / vdev / membro", "Ruolo o tipo", "Stato", "GUID"])
        self.tree.setMinimumWidth(610)
        self.tree.itemSelectionChanged.connect(self.tree_selected)
        self.detail = QTextBrowser()
        split.addWidget(self.tree)
        split.addWidget(self.detail)
        split.setSizes([820, 460])
        layout.addWidget(split, 1)
        self.issues = QLabel()
        self.issues.setWordWrap(True)
        layout.addWidget(self.issues)
        self.tabs.addTab(tab, "Panoramica")

    def _disks(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        note = QLabel("Identifica sempre il supporto con seriale e posizione. SATA diretto può non offrire LED comandabili.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.disk_table = QTableWidget(0, 8)
        self.disk_table.setHorizontalHeaderLabels(["Disco", "Seriale", "Modello", "Capacità", "Uso", "SMART", "Posizione", "LED locate"])
        self.disk_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.disk_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.disk_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.disk_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.disk_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.disk_table, 1)
        actions = QHBoxLayout()
        self.button("Registra posizione / etichetta", self.edit_position, actions, network=False)
        self.button("Identifica per 30 secondi", self.identify, actions)
        self.button("Mostra diagnostica SMART", self.show_smart, actions, network=False)
        actions.addStretch()
        layout.addLayout(actions)
        self.tabs.addTab(tab, "Dischi")

    def _replacement(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form_group = QGroupBox("Selezione verificata")
        form = QFormLayout(form_group)
        self.pool_combo, self.old_combo, self.target_combo = QComboBox(), QComboBox(), QComboBox()
        self.profile = QComboBox()
        self.profile.addItems(["Laboratorio · spegnimento obbligatorio", "Produzione · hot swap da verificare"])
        form.addRow("Pool", self.pool_combo)
        form.addRow("Membro da sostituire", self.old_combo)
        form.addRow("Nuovo disco vuoto", self.target_combo)
        form.addRow("Profilo hardware", self.profile)
        self.hot_swap = QCheckBox("Ho verificato hot swap su questo server e su questo vano")
        form.addRow(self.hot_swap)
        self.hot_swap.setEnabled(False)
        self.pool_combo.currentIndexChanged.connect(self.pool_changed)
        self.old_combo.currentIndexChanged.connect(self.invalidate_plan)
        self.target_combo.currentIndexChanged.connect(self.invalidate_plan)
        self.profile.currentIndexChanged.connect(self.profile_changed)
        self.hot_swap.toggled.connect(self.invalidate_plan)
        layout.addWidget(form_group)
        actions = QHBoxLayout()
        self.button("Genera piano e comandi", self.preview, actions, "primary")
        self.button("Sostituzione fisica", self.physical_instructions, actions, network=False)
        self.button("Prepara rimozione a caldo", self.preview_offline, actions)
        self.button("Chiarisci RAID", self.ask_raid, actions, network=False)
        self.button("Spegni laboratorio", self.shutdown, actions, "danger")
        layout.addLayout(actions)
        self.plan_view = QTextBrowser()
        self.plan_view.setPlaceholderText("Seleziona pool, membro guasto e nuovo disco. Nessuna modifica parte dall'anteprima.")
        layout.addWidget(self.plan_view, 1)
        approval = QHBoxLayout()
        self.backup = QCheckBox("Backup verificato e manutenzione concordata")
        self.physical = QCheckBox("Identità e posizione del nuovo disco verificate")
        approval.addWidget(self.backup)
        approval.addWidget(self.physical)
        self.execute_button = self.button("Approva ed esegui", self.execute, approval, "danger")
        self.execute_button.setEnabled(False)
        layout.addLayout(approval)
        self.tabs.addTab(tab, "Sostituzione")

    def _jobs(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        actions = QHBoxLayout()
        self.button("Recupera interventi dal nodo", self.refresh_jobs, actions)
        self.button("Registra prova di avvio autonomo", self.record_boot_result, actions)
        actions.addStretch()
        layout.addLayout(actions)
        self.job_table = QTableWidget(0, 4)
        self.job_table.setHorizontalHeaderLabels(["Intervento", "Pool", "Destinazione", "Stato"])
        self.job_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.job_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.job_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.job_table.itemSelectionChanged.connect(self.job_selected)
        layout.addWidget(self.job_table)
        self.job_detail = QTextBrowser()
        layout.addWidget(self.job_detail, 1)
        self.job_progress = QProgressBar()
        self.job_progress.setRange(0, 100)
        self.job_progress.setFormat("Resilver · %p% (stima)")
        self.job_progress.hide()
        layout.addWidget(self.job_progress)
        note = QLabel("Chiudere l'app non annulla una modifica sul nodo. Un esito UNKNOWN richiede verifica, mai ripetizione automatica. Il test di boot resta separato.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs.addTab(tab, "Interventi")

    def _connection(self):
        tab = QWidget()
        outer = QVBoxLayout(tab)
        group = QGroupBox("Connessione SSH")
        form = QFormLayout(group)
        saved = self.store.get("connection", {})
        self.host = QLineEdit(saved.get("host", ""))
        self.host.setPlaceholderText("Indirizzo del tuo nodo Proxmox")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(saved.get("port", 22))
        self.username = QLineEdit(saved.get("username", "root"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Facoltativa · solo in memoria per questa sessione")
        self.key_file = QLineEdit(saved.get("key_file", ""))
        self.key_file.setPlaceholderText("Facoltativa con password o agente SSH")
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_file)
        self.button("Sfoglia", self.choose_key, key_row, network=False)
        self.sudo = QCheckBox("Usa sudo -n limitato all'helper già installato")
        self.sudo.setChecked(saved.get("sudo", False))
        form.addRow("Server", self.host)
        form.addRow("Porta", self.port)
        form.addRow("Utente", self.username)
        form.addRow("Chiave privata", key_row)
        form.addRow("Password / passphrase chiave", self.password)
        form.addRow(self.sudo)
        self.read_only = QCheckBox('Sola lettura e anteprima: nessuna esecuzione')
        self.read_only.setChecked(saved.get('read_only', False))
        form.addRow(self.read_only)
        retention = QSpinBox()
        retention.setRange(10, 10000)
        retention.setValue(self.store.get('retention', 100))
        retention.valueChanged.connect(lambda value: self.store.set('retention', value))
        form.addRow('Snapshot da conservare', retention)
        self.server_profiles = QComboBox()
        self.server_profiles.addItems(sorted(self.store.get('profiles', {})))
        form.addRow('Profilo cliente / nodo / ambiente', self.server_profiles)
        self.button('Salva profilo', self.save_profile, outer, network=False)
        self.button('Carica profilo', self.load_profile, outer, network=False)
        outer.addWidget(group)
        buttons = QHBoxLayout()
        self.button("Collega e analizza", self.connect_live, buttons, "primary")
        self.button("Installa / aggiorna helper sul nodo", self.install_helper, buttons)
        buttons.addStretch()
        outer.addLayout(buttons)
        info = QTextBrowser()
        info.setHtml("<h3>Primo collegamento</h3><ol><li>Confronta l'impronta SSH con quella del server, poi accettala.</li>"
                     "<li>Installa esplicitamente l'helper con una sessione root: il pacchetto usa solo la libreria standard Python.</li>"
                     "<li>Avvia l'analisi. I comandi modificativi richiedono un piano approvato.</li></ol>"
                     "<p>L'helper è salvato in <code>/usr/local/lib/pve-disk-assistant/helper.pyz</code>; piani ed esiti in "
                     "<code>/var/lib/pve-disk-assistant</code>. Non installa pacchetti né un servizio web sul nodo.</p>"
                     "<p>Le credenziali non vengono scritte nel database. La chiave host viene conservata localmente; "
                     "una chiave cambiata è rifiutata. Per utenti non root, vedere le regole sudo nel manuale.</p>"
                     "<p>Prima delle prove reali usare dati sacrificabili. La compatibilità hardware non è certificata dalla demo.</p>")
        outer.addWidget(info, 1)
        self.tabs.addTab(tab, "Connessione")

    def _boot_diagnostics(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        text = QLabel("ZFS ONLINE non certifica il boot. Ogni ESP è controllata separatamente; l'avvio autonomo richiede una prova fisica a PC spento durante il cambio dischi.")
        text.setWordWrap(True)
        layout.addWidget(text)
        actions = QHBoxLayout()
        self.button("Aggiorna diagnostica EFI", self.scan, actions, "primary")
        self.button("Piano ripristino della sola ESP selezionata", lambda: self.preview_boot_repair(), actions)
        self.button('Piano pulizia UUID assenti', lambda: self.preview_boot_repair(action='clean_boot'), actions)
        actions.addStretch()
        layout.addLayout(actions)
        self.boot_table = QTableWidget(0, 8)
        self.boot_table.setHorizontalHeaderLabels(["Pool", "Seriale", "ESP", "FAT32 / coerenza", "Registro", "Loader / kernel", "Percorso UEFI", "Esito"])
        self.boot_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.boot_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.boot_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.boot_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.boot_table.horizontalHeader().setStretchLastSection(True)
        self.boot_table.itemSelectionChanged.connect(self.boot_selected)
        layout.addWidget(self.boot_table, 1)
        self.boot_detail = QTextBrowser()
        layout.addWidget(self.boot_detail, 1)
        self.tabs.addTab(tab, "Avvio EFI")

    def render_boot(self):
        self.boot_rows = []
        reports = []
        for pool in self.snapshot["pools"]:
            if pool.get("role") not in {"BOOT", "MISTO"}:
                continue
            report = boot_report(self.snapshot, pool)
            reports.append(report)
            self.boot_rows.extend({"pool": pool, "disk": disk, "report": report} for disk in report["disks"])
        self.boot_table.setRowCount(len(self.boot_rows))
        for index, item in enumerate(self.boot_rows):
            disk = item["disk"]
            checks = disk["checks"]
            def states(*keys):
                return " / ".join(checks.get(k, {}).get("state", "UNKNOWN") for k in keys)
            values = [item["pool"]["name"], disk["serial"] or "Membro assente", (disk.get("esp") or {}).get("path", "Non rilevata"),
                      states("fat32", "filesystem"), states("registration"), states("loader", "configuration"), states("uefi_path"), disk.get("state", "UNKNOWN")]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 7:
                    cell.setForeground(color_for(value))
                self.boot_table.setItem(index, col, cell)
        ready = reports and all(r["configuration_ready"] and not r["warnings"] for r in reports)
        value = "CONFIG. OK" if ready else "DA VERIFICARE" if reports else "NON RILEVATA"
        self.card_values["boot"].setText(value)
        self.card_values["boot"].setStyleSheet("color:" + ("#71d7b3" if ready else "#f4bf65"))
        if self.boot_rows:
            self.boot_table.selectRow(0)
            self.boot_selected()
        else:
            self.boot_detail.clear()

    def boot_selected(self):
        index = self.boot_table.currentRow()
        if index < 0 or index >= len(self.boot_rows):
            return
        entry = self.boot_rows[index]
        disk = entry["disk"]
        e = html.escape
        body = f"<h2>{e(disk['serial'] or 'Membro non identificato')}</h2><p>Firmware: {e(entry['report']['firmware_mode'])} · loader: {e(disk['mode'])}</p>"
        body += "<p>Lettura voci UEFI (efibootmgr): " + ("disponibile" if entry["report"]["nvram_readable"] else "non disponibile") + ".</p>"
        for name, check in disk["checks"].items():
            body += f"<p><b>{e(name)} · {e(check['state'])}</b><br>{e(check['detail'])}</p>"
        if entry["report"]["warnings"]:
            body += "<p>Avvisi registro ESP: " + e("; ".join(entry["report"]["warnings"])) + "</p>"
        body += "<p><b>Avvio autonomo: NON TESTATO.</b> Configurazione e file non provano che il firmware avvierà il disco. Secure Boot: firme non certificate dall'app.</p>"
        self.boot_detail.setHtml(body)

    def preview_boot_repair(self, action='repair_boot'):
        index = self.boot_table.currentRow()
        if index < 0 or index >= len(self.boot_rows):
            return
        row = self.boot_rows[index]
        if not row["disk"]["by_id"]:
            QMessageBox.information(self, "ESP", "Disco fisico non identificato: nessun ripristino automatico.")
            return
        self.invalidate_plan()
        self.pool_combo.setCurrentIndex(self.pool_combo.findData(row["pool"]["guid"]))
        request = {"action": action, "pool_guid": row["pool"]["guid"], "target": row["disk"]["by_id"]}
        def show(plan):
            self.tabs.setCurrentIndex(2)
            self.render_plan(plan)
        self.asynchronous(lambda: self.backend.rpc({"op": "plan", "request": request}), show, "Verifica del piano di ripristino della sola ESP…")

    def asynchronous(self, function, callback, label, retry=True):
        if self.busy:
            return
        self.busy = True
        controls = [self.tree, self.pool_combo, self.old_combo, self.target_combo, self.profile, self.hot_swap, self.server_profiles]
        for control in controls:
            control.setEnabled(False)
        backend = self.backend
        for button in self.network_buttons:
            button.setEnabled(False)
        self.statusBar().showMessage(label)
        worker = Worker(function, self)
        self.worker = worker
        worker.result.connect(lambda result: callback(result) if self.backend is backend else None)

        def failed(error):
            if isinstance(error, HostVerificationRequired) and isinstance(self.backend, SSHBackend):
                answer = QMessageBox.question(self, "Verifica identità SSH", str(error) +
                    "\n\nConfronta questa impronta con una fonte affidabile sul server. Corrisponde e vuoi memorizzarla?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self.backend.trust(error)
                    if retry:
                        QTimer.singleShot(100, lambda: self.asynchronous(function, callback, label, retry=False))
                return
            self.statusBar().showMessage("Operazione non completata; dati precedenti non aggiornati.")
            self.last_error = str(error).splitlines()[0]
            self.freshness.setText("DATI NON AGGIORNATI · " + str(error).splitlines()[0])
            QMessageBox.warning(self, "Operazione non completata", str(error))

        def finished():
            self.busy = False
            for control in controls:
                control.setEnabled(True)
            for button in self.network_buttons:
                button.setEnabled(True)
            self.execute_button.setEnabled(bool(self.plan and not self.plan.get('read_only') and not self.plan["blocks"] and not self.is_demo()))
            worker.deleteLater()
            self.worker = None

        worker.error.connect(failed)
        worker.finished.connect(finished)
        worker.start()

    def is_demo(self):
        return isinstance(self.backend, DemoBackend)

    def connection_from_form(self):
        if not self.host.text().strip() or not self.username.text().strip():
            QMessageBox.information(self, "Connessione", "Inserisci server e utente SSH.")
            return None
        config = {"host": self.host.text().strip(), "port": self.port.value(),
                  "username": self.username.text().strip(), "key_file": self.key_file.text().strip(),
                  "sudo": self.sudo.isChecked(), 'read_only': self.read_only.isChecked()}
        self.store.set("connection", config)
        return SSHBackend(Connection(**config, password=self.password.text()), self.store.directory / "known_hosts")

    def save_profile(self):
        name, ok = QInputDialog.getText(self, 'Profilo', 'Cliente / nodo / ambiente:')
        if ok and name.strip() and self.connection_from_form():
            profiles = self.store.get('profiles', {})
            profiles[name.strip()] = self.store.get('connection')
            self.store.set('profiles', profiles)
            self.server_profiles.clear()
            self.server_profiles.addItems(sorted(profiles))
            self.server_profiles.setCurrentText(name.strip())

    def load_profile(self):
        if self.busy:
            return
        config = self.store.get('profiles', {}).get(self.server_profiles.currentText())
        if not config:
            return
        for widget, key in ((self.host, 'host'), (self.username, 'username'), (self.key_file, 'key_file')):
            widget.setText(config.get(key, ''))
        self.port.setValue(config.get('port', 22))
        self.sudo.setChecked(config.get('sudo', False))
        self.read_only.setChecked(config.get('read_only', False))
        self.password.clear()
        self.backend = None
        self.snapshot = None
        self.invalidate_plan()
        self.badge.setText('Profilo caricato: premi Collega e analizza')

    def connect_live(self):
        backend = self.connection_from_form()
        if backend:
            self.backend, self.snapshot, self.jobs = backend, None, []
            self.invalidate_plan()
            self.badge.setText("SSH · " + backend.connection.host)
            self.scan()

    def install_helper(self):
        backend = self.connection_from_form()
        if not backend:
            return
        msg = ("Installa o aggiorna il nostro helper Python sul nodo " + backend.connection.host +
               ".\n\nScrive /usr/local/lib/pve-disk-assistant/helper.pyz. Non modifica pool o dischi. "
               "Non aggiornare mentre è in corso un intervento. Procedere?")
        if QMessageBox.question(self, "Installazione helper", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.backend, self.snapshot, self.jobs = backend, None, []
        self.invalidate_plan()
        self.asynchronous(backend.install, lambda r: QMessageBox.information(self, "Helper installato",
            "Helper installato. Ora premi Collega e analizza.\nSHA256: " + r["sha256"]), "Installazione helper…")

    def choose_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Seleziona chiave privata SSH")
        if path:
            self.key_file.setText(path)

    def load_demo(self):
        self.backend = DemoBackend(self.demo_raid.currentIndex() == 1, boot_fault=self.demo_raid.currentIndex() == 2)
        self.snapshot, self.plan, self.jobs = None, None, []
        self.badge.setText("DEMO · nessun comando reale")
        self.scan()

    def scan(self):
        if self.backend is None:
            self.tabs.setCurrentIndex(4)
            return
        self.asynchronous(lambda: self.backend.rpc({"op": "collect"}), self.render_snapshot, "Lettura del nodo e analisi ZFS…")

    def render_snapshot(self, snapshot):
        previous = self.snapshot
        if previous and previous['host_id'] == snapshot['host_id'] and snapshot['collected_at'] < previous['collected_at']:
            return
        combos = [self.pool_combo, self.old_combo, self.target_combo]
        choices = [c.currentData() for c in combos]
        selected = self.tree.currentItem()
        tree_key = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        disk = self.snapshot['disks'][self.disk_table.currentRow()].get('by_id') if self.snapshot and 0 <= self.disk_table.currentRow() < len(self.snapshot['disks']) else None
        boot_key = self.boot_rows[self.boot_table.currentRow()]['disk']['by_id'] if 0 <= self.boot_table.currentRow() < len(self.boot_rows) else None
        widgets = [self.tree, self.disk_table, self.boot_table, *combos]
        scrolls = [(w, w.verticalScrollBar().value(), w.horizontalScrollBar().value()) for w in widgets[:3]]
        plan = self.plan
        checks = (self.backup.isChecked(), self.physical.isChecked())
        for w in widgets:
            w.blockSignals(True)
        try:
            self._render_snapshot(snapshot)
            if previous:
                self.pool_combo.setCurrentIndex(self.pool_combo.findData(choices[0]))
                self.pool_changed()
                for c, choice in zip(combos[1:], choices[1:]):
                    c.setCurrentIndex(c.findData(choice))
            self.tree.clearSelection()
            def restore(item):
                if item.data(0, Qt.ItemDataRole.UserRole) == tree_key:
                    self.tree.setCurrentItem(item)
                for i in range(item.childCount()):
                    restore(item.child(i))
            for i in range(self.tree.topLevelItemCount()):
                restore(self.tree.topLevelItem(i))
            self.disk_table.clearSelection()
            self.disk_table.setCurrentCell(-1, -1)
            for i, d in enumerate(snapshot['disks']):
                if disk and d.get('by_id') == disk:
                    self.disk_table.selectRow(i)
            if previous:
                self.boot_table.clearSelection()
                self.boot_table.setCurrentCell(-1, -1)
                for i, row in enumerate(self.boot_rows):
                    if boot_key and row['disk']['by_id'] == boot_key:
                        self.boot_table.selectRow(i)
            self.boot_detail.clear()
            self.boot_selected()
            if plan and previous and safety_fingerprint(previous) == safety_fingerprint(snapshot):
                self.plan = plan
                self.render_plan(plan)
                self.backup.setChecked(checks[0])
                self.physical.setChecked(checks[1])
            elif plan:
                self.plan_view.setText('Piano invalidato: sono cambiati dati rilevanti. Genera una nuova anteprima.')
            if previous and choices[0] is not None and self.pool_combo.currentIndex() < 0:
                self.issues.setText('Pool selezionato non più disponibile: operazioni sospese. Seleziona esplicitamente un pool.')
            for w, vertical, horizontal in scrolls:
                w.verticalScrollBar().setValue(vertical)
                w.horizontalScrollBar().setValue(horizontal)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _render_snapshot(self, snapshot):
        self.snapshot = snapshot
        self.last_error = ""
        self.store.snapshot(snapshot)
        self.invalidate_plan()
        self.badge.setText("DEMO · dati sintetici" if self.is_demo() else "SSH · " + snapshot["hostname"])
        self.card_values["pools"].setText(str(len(snapshot["pools"])))
        self.card_values["disks"].setText(str(len(snapshot["disks"])))
        self.card_values["bad"].setText(str(sum(n["state"] not in GOOD for p in snapshot["pools"] for n in leaves(p))))
        self.card_values["root"].setText(snapshot["root"].get("fstype", "?").upper())
        self.tree.clear()
        for pool in snapshot["pools"]:
            item = QTreeWidgetItem([pool["name"], pool["role"], pool["state"], pool["guid"]])
            item.setData(0, Qt.ItemDataRole.UserRole, (pool["guid"], None))
            item.setForeground(2, color_for(pool["state"]))
            self.tree.addTopLevelItem(item)

            def append(parent, nodes):
                for node in nodes:
                    disk = disk_for_member(snapshot, node)
                    label = disk["serial"] if disk and not node["children"] else node["name"]
                    child = QTreeWidgetItem([label, node["kind"].upper(), node["state"], node["guid"]])
                    child.setToolTip(0, node["name"])
                    child.setData(0, Qt.ItemDataRole.UserRole, (pool["guid"], node["guid"] or node["name"]))
                    child.setForeground(2, color_for(node["state"]))
                    parent.addChild(child)
                    append(child, node["children"])
            append(item, pool["children"])
        self.tree.expandAll()
        for col in range(3):
            self.tree.resizeColumnToContents(col)
        self._building = True
        previous = self.pool_combo.currentData()
        self.pool_combo.clear()
        for pool in snapshot["pools"]:
            self.pool_combo.addItem(f"{pool['name']} · {pool['role']} · {raid_label(pool)}", pool["guid"])
        index = self.pool_combo.findData(previous)
        self.pool_combo.setCurrentIndex(index if previous is not None else -1)
        self._building = False
        self.pool_changed()
        self.render_disks()
        self.render_boot()
        problems = snapshot["issues"] + [w for p in snapshot["pools"] for w in p.get("warnings", [])]
        problems += snapshot.get('hardware_notes', [])
        if snapshot.get("helper_version") != __version__:
            problems.append("Helper da aggiornare alla versione " + __version__ + ": piani e interventi non abilitati.")
        if any(not row["report"]["configuration_ready"] for row in self.boot_rows):
            problems.append("Configurazione EFI non verificata su entrambi i membri: apri Avvio EFI, anche con pool ONLINE.")
        self.issues.setText("ATTENZIONE · " + " | ".join(problems) if problems else
            "Inventario acquisito · il rilevamento non sostituisce il collaudo hardware e la verifica dei backup.")
        self.issues.setStyleSheet("color:" + ("#f4bf65" if problems else "#96a9c6"))
        self._age()
        self.statusBar().showMessage("Analisi aggiornata · " + snapshot["pve_version"])
        for pool in snapshot["pools"]:
            unknown = (pool.get("warnings") or not data_groups(pool)
                       or any(g["kind"] == "disk" and g["children"] for g in data_groups(pool)))
            key = (snapshot["host_id"], pool["guid"], str(pool.get("warnings")))
            if unknown and key not in self.raid_questions:
                self.raid_questions.add(key)
                self.issues.setText(self.issues.text() + ' · Topologia da chiarire: seleziona il pool e usa il pulsante di chiarimento RAID.')
                break

    def _age(self):
        if not self.snapshot:
            return
        try:
            age = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(self.snapshot["collected_at"])).total_seconds()))
            stale = " · DATI NON AGGIORNATI" if age > 90 or self.last_error else ""
            self.freshness.setText(f"{self.snapshot['hostname']} · ultima lettura {age} s fa{stale} · {self.snapshot.get('guest_scope', '')}")
        except (ValueError, TypeError):
            self.freshness.setText("Data della raccolta non verificabile")

    def render_disks(self):
        if not self.snapshot:
            return
        self.disk_table.setRowCount(len(self.snapshot["disks"]))
        for row, disk in enumerate(self.snapshot["disks"]):
            pools = [p["name"] for p in self.snapshot["pools"] if any(disk_for_member(self.snapshot, m) == disk for m in leaves(p))]
            smart = disk.get("smart", {})
            health = "ALLARME" if smart.get("passed") is False or smart.get("exit_code", 0) & 248 else "OK" if smart.get("passed") is True and not smart.get("exit_code", 0) & 7 else "NON NOTO"
            usage = ", ".join(pools) or ("Firme / partizioni" if disk.get("partitions") or disk.get("signatures") else "Libero da verificare")
            values = [disk["path"], disk["serial"], disk.get("model", ""), size_text(disk["size"]), usage, health,
                      self.store.position(self.snapshot["host_id"], disk["serial"]), "Disponibile" if disk.get("locate_paths") else "Non rilevato"]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 5:
                    item.setForeground(color_for("FAULTED" if health == "ALLARME" else "ONLINE" if health == "OK" else "UNKNOWN"))
                self.disk_table.setItem(row, col, item)

    def pool_changed(self, *_):
        if self._building or not self.snapshot:
            return
        self.invalidate_plan()
        pool = self.current_pool()
        self.old_combo.clear()
        if pool:
            for member in leaves(pool):
                if member["state"] in BAD:
                    disk = disk_for_member(self.snapshot, member)
                    label = disk["serial"] if disk else member["name"]
                    self.old_combo.addItem(f"{label} · {member['state']} · GUID {member['guid']}", member["guid"])
        self.target_combo.clear()
        used = [disk_for_member(self.snapshot, m) for p in self.snapshot["pools"] for m in leaves(p)]
        for disk in self.snapshot["disks"]:
            if disk not in used and disk.get("by_id") and not disk.get("partitions") and not disk.get("signatures"):
                self.target_combo.addItem(f"{disk['serial']} · {size_text(disk['size'])} · {disk['path']}", disk["by_id"])

    def current_pool(self):
        return next((p for p in (self.snapshot or {}).get("pools", []) if p["guid"] == self.pool_combo.currentData()), None)

    def member_disk(self, member):
        if not member or not self.snapshot:
            return None, False
        current = disk_for_member(self.snapshot, member)
        if current:
            return current, False
        pool = next((p for p in self.snapshot["pools"] if member in leaves(p)), None)
        previous = self.store.member_identity(self.snapshot["host_id"], pool["guid"], member["guid"]) if pool else None
        if not previous and member.get("was_path", member["name"]).startswith("/dev/disk/"):
            previous = self.store.historical_disk(self.snapshot["host_id"], member.get("was_path", member["name"]))
        return previous, bool(previous)

    def invalidate_plan(self, *_):
        self.plan = None
        if hasattr(self, "execute_button"):
            self.execute_button.setEnabled(False)
            self.plan_view.clear()
            self.backup.setChecked(False)
            self.physical.setChecked(False)

    def profile_changed(self, *_):
        production = self.profile.currentIndex() == 1
        self.hot_swap.setEnabled(production)
        self.hot_swap.setChecked(False)
        self.invalidate_plan()

    def tree_selected(self):
        if not self.snapshot or not self.tree.selectedItems():
            return
        pool_id, member_id = self.tree.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole)
        pool = next(p for p in self.snapshot["pools"] if p["guid"] == pool_id)
        self.pool_combo.setCurrentIndex(self.pool_combo.findData(pool_id))
        body = f"<h2>{html.escape(pool['name'])}</h2><p>{html.escape(pool['role'])} · {html.escape(raid_label(pool))}</p>"
        body += "<p><b>Stato:</b> " + html.escape(pool["state"]) + "<br><b>Scan:</b> " + html.escape(pool["scan"]) + "</p>"
        metrics = self.snapshot.get('pool_metrics', {}).get(pool['name'], {})
        body += '<p>Capacità utilizzata: ' + html.escape(str(metrics.get('capacity_percent', 'N/D'))) + '% · Frammentazione: ' + html.escape(str(metrics.get('fragmentation_percent', 'N/D'))) + '%</p>'
        if member_id:
            member = next((m for m in leaves(pool) if (m["guid"] or m["name"]) == member_id), None)
            if member:
                disk, historical = self.member_disk(member)
                body += "<h3>Membro selezionato</h3><p>" + html.escape(member["name"]) + "<br>GUID " + html.escape(member["guid"]) + "</p>"
                if disk:
                    position = self.store.position(self.snapshot["host_id"], disk["serial"]) or "Non registrata"
                    body += f"<p><b>Seriale:</b> {html.escape(disk['serial'])}<br><b>Posizione:</b> {html.escape(position)}</p>"
                    if historical:
                        body += "<p>Associazione ricavata dallo storico: disco attualmente assente. Riconfermare la mappa fisica se sono stati spostati i cavi.</p>"
                else:
                    body += "<p>Disco assente o identità fisica non risolta. Usare la mappa registrata prima del guasto.</p>"
                idx = self.old_combo.findData(member["guid"])
                if idx >= 0:
                    self.old_combo.setCurrentIndex(idx)
        body += "<h3>Carichi dipendenti dal pool</h3>"
        guests = pool.get("guests", [])
        body += "<ul>" + "".join(f"<li>{html.escape(g['kind'])} {html.escape(g['id'])} · {html.escape(g['name'])}</li>" for g in guests) + "</ul>" if guests else "<p>Nessun carico associato nelle configurazioni lette.</p>"
        body += "<p>La dipendenza non significa che la sostituzione interromperà necessariamente il carico. Verificare storage non ZFS e configurazioni esterne.</p>"
        self.detail.setHtml(body)

    def selected_disk(self):
        row = self.disk_table.currentRow()
        if not self.snapshot or row < 0:
            QMessageBox.information(self, "Disco", "Seleziona un disco nella tabella.")
            return None
        return self.snapshot["disks"][row]

    def edit_position(self):
        disk = self.selected_disk()
        if disk:
            position, ok = QInputDialog.getText(self, "Posizione fisica", "Etichetta o vano per " + disk["serial"],
                text=self.store.position(self.snapshot["host_id"], disk["serial"]))
            if ok:
                self.store.set_position(self.snapshot["host_id"], disk["serial"], position.strip())
                self.render_disks()

    def identify(self):
        disk = self.selected_disk()
        if not disk:
            return
        if not disk.get("locate_paths"):
            QMessageBox.information(self, "LED non disponibile", "Nessun LED locate associato tramite enclosure. Usa il seriale " + disk["serial"] + " e l'etichetta fisica.")
            return
        if QMessageBox.question(self, "Identificazione fisica", "Attivare per 30 secondi il LED locate associato a " + disk["serial"] +
                                "? Verifica visivamente che corrisponda al vano atteso.") == QMessageBox.StandardButton.Yes:
            self.asynchronous(lambda: self.backend.rpc({"op": "identify", "target": disk["by_id"], "seconds": 30}),
                              lambda r: QMessageBox.information(self, "Identificazione", r["message"]), "Identificazione LED…")

    def show_smart(self):
        disk = self.selected_disk()
        if disk:
            box = QMessageBox(self)
            box.setWindowTitle("Diagnostica SMART · " + disk["serial"])
            box.setText("Stato SMART: " + str(disk.get("smart", {}).get("passed", "non noto")) + "\nDati relativi all'ultima analisi, non una garanzia di integrità.")
            box.setDetailedText(json.dumps(disk.get("smart", {}), indent=2, ensure_ascii=False))
            raw = disk.get('smart', {}).get('raw', {})
            nvme = raw.get('nvme_smart_health_information_log', {})
            box.setInformativeText('Temperatura: ' + str(disk.get('smart', {}).get('temperature', 'N/D')) +
                '\nOre: ' + str(raw.get('power_on_time', {}).get('hours', nvme.get('power_on_hours', 'N/D'))) +
                '\nNVMe usura %: ' + str(nvme.get('percentage_used', 'N/D')) +
                '\nNVMe errori media: ' + str(nvme.get('media_errors', 'N/D')) +
                '\nNVMe critical warning: ' + str(nvme.get('critical_warning', 'N/D')) +
                '\nWWN: ' + str(disk.get('wwn', 'N/D')))
            box.exec()

    def ask_raid(self):
        pool = self.current_pool()
        if not pool:
            return
        selection, ok = QInputDialog.getItem(self, "Chiarimento configurazione RAID", "Quale configurazione ti aspetti per " + pool["name"] + "?",
            ["Non lo so", "Mirror", "RAIDZ1", "RAIDZ2", "RAIDZ3", "Altra configurazione"], editable=False)
        if ok:
            self.store.set("expected:" + self.snapshot["host_id"] + ":" + pool["guid"], selection)
            QMessageBox.information(self, "Configurazione dichiarata", "Dichiarazione registrata: " + selection +
                ".\nRilevamento attuale: " + raid_label(pool) +
                ".\nLa dichiarazione non abilita scritture: aggiornare la raccolta e verificare i dati del nodo. Se il parsing fallisce, esporta un rapporto e consulta il manuale.")

    def physical_instructions(self):
        pool = self.current_pool()
        if not pool:
            return
        member = next((m for m in leaves(pool) if m["guid"] == self.old_combo.currentData()), None)
        disk, historical = self.member_disk(member)
        identity = disk["serial"] if disk else "Non disponibile: richiede mappa fisica verificata"
        position = self.store.position(self.snapshot["host_id"], disk["serial"]) if disk else ""
        text = f"Pool: {pool['name']}\nRAID: {raid_label(pool)}\nDisco: {identity}\nPosizione: {position or 'Non registrata'}\n\n"
        if historical:
            text += "Identità dallo storico: riconfermare la mappa prima della rimozione.\n\n"
        if self.profile.currentIndex() == 0:
            text += ("1. Registra seriale, GUID e posizione prima dello spegnimento.\n"
                     "2. Arresta ordinatamente VM/CT dalla GUI Proxmox.\n"
                     "3. Spegni il nodo e verifica fisicamente che sia spento: SSH assente non basta.\n"
                     "4. Sostituisci il solo disco identificato con il ricambio vuoto.\n"
                     "5. Accendi manualmente il PC e premi Aggiorna analisi.\n"
                     "6. Seleziona il nuovo seriale e genera il piano ZFS/boot.\n\n"
                     "Queste istruzioni rimangono disponibili sul PC Windows durante il fermo.")
        else:
            text += ("Verifica la procedura hot swap del server e l'identità del vano. Se il membro problematico risponde ancora, "
                     "usa Prepara rimozione a caldo per approvare il comando offline. Attendi READY_FOR_REMOVAL negli Interventi; non rimuovere altri membri. "
                     "Dopo l'inserimento aggiorna l'analisi e riconosci il nuovo seriale.")
        QMessageBox.information(self, "Sostituzione fisica", text)

    def preview(self):
        pool = self.current_pool()
        if not pool:
            return
        if not data_groups(pool) or pool.get("warnings") or any(g["kind"] not in {"mirror", "raidz1", "raidz2", "raidz3", "disk", "draid"} for g in data_groups(pool)):
            self.ask_raid()
        if not self.old_combo.currentData() or not self.target_combo.currentData():
            QMessageBox.information(self, "Piano", "Serve un membro guasto o escluso e un ricambio vuoto riconosciuto. Prima della sostituzione fisica consulta le istruzioni; poi aggiorna l'analisi.")
            return
        request = {"pool_guid": pool["guid"], "old_guid": self.old_combo.currentData(), "target": self.target_combo.currentData(),
                   "profile": "laboratorio" if self.profile.currentIndex() == 0 else "produzione",
                   "hot_swap_confirmed": self.hot_swap.isChecked()}
        self.asynchronous(lambda: self.backend.rpc({"op": "plan", "request": request}), self.render_plan,
                          "Generazione del piano sul nodo e verifica dei prerequisiti…")

    def preview_offline(self):
        pool = self.current_pool()
        if not pool or not self.old_combo.currentData():
            QMessageBox.information(self, "Rimozione", "Seleziona il membro problematico del pool.")
            return
        request = {"action": "offline", "pool_guid": pool["guid"], "old_guid": self.old_combo.currentData(),
                   "profile": "produzione" if self.profile.currentIndex() == 1 else "laboratorio",
                   "hot_swap_confirmed": self.hot_swap.isChecked()}
        self.asynchronous(lambda: self.backend.rpc({"op": "plan", "request": request}), self.render_plan, "Verifica della rimozione a caldo…")

    def render_plan(self, plan):
        self.plan = plan
        e = html.escape
        label = "Disco da escludere" if plan.get("action") == "offline" else "Disco di cui ripristinare SOLO l'ESP" if plan.get("action") in {"repair_boot", "clean_boot"} else "Nuovo disco"
        self.physical.setText("Identità e posizione del disco da escludere verificate" if plan.get("action") == "offline" else
                              "Identità del disco e della sola ESP da ripristinare verificate" if plan.get("action") in {"repair_boot", "clean_boot"} else "Identità e posizione del nuovo disco verificate")
        body = f"<h2>{e(plan['pool'])} · {e(plan['role'])}</h2><p>{e(plan['raid'])}<br>{label}: <b>{e(plan['target']['serial'])}</b> · {e(plan['target']['by_id'])}</p>"
        body += '<p>Nodo: ' + e((self.snapshot or {}).get('hostname', '?')) + ' · Pool GUID: ' + e(plan['pool_guid']) + '<br>Snapshot di sicurezza: ' + e(plan['fingerprint']) + '</p>'
        if plan.get('action') == 'clean_boot':
            body += '<h3>Pulizia del registro ESP: confermare che gli UUID assenti non appartengano a dischi temporaneamente scollegati.</h3>'
        if plan["blocks"]:
            body += "<h3 style='color:#f18c99'>Esecuzione bloccata</h3><ul>" + "".join("<li>" + e(b) + "</li>" for b in plan["blocks"]) + "</ul>"
        else:
            body += "<h3 style='color:#71d7b3'>Prerequisiti verificati · approvazione richiesta</h3>"
        body += "<ol>" + "".join(f"<li><pre>{e(s['command'])}</pre><p>{e(s['explanation'])}</p></li>" for s in plan["steps"]) + "</ol>"
        body += "<h3>Note</h3><ul>" + "".join("<li>" + e(w) + "</li>" for w in plan["warnings"]) + "</ul>"
        body += "<p>Anteprima con controlli di sola lettura, non una simulazione nativa universale. Validità: dieci minuti e solo con inventario invariato.</p>"
        if plan.get("role") in {"BOOT", "MISTO"} and plan.get("action") != "offline":
            body += "<p><b>Esito boot separato da ZFS.</b> In una sostituzione la fase EFI attende il resilver. Il completamento richiede configurazione EFI di entrambi i membri e prove di avvio autonomo distinte.</p>"
        self.plan_view.setHtml(body)
        self.execute_button.setEnabled(not plan["blocks"] and not plan.get('read_only') and not self.is_demo())

    def execute(self):
        plan = self.plan
        if not plan or plan.get('read_only') or plan["blocks"] or self.is_demo():
            return
        if not self.backup.isChecked() or not self.physical.isChecked():
            QMessageBox.information(self, "Approvazione", "Conferma backup/manutenzione e identificazione fisica del ricambio.")
            return
        label = "disco da escludere" if plan.get("action") == "offline" else "disco di cui ripristinare SOLO l'ESP" if plan.get("action") in {"repair_boot", "clean_boot"} else "disco nuovo da inizializzare"
        serial, ok = QInputDialog.getText(self, "Approvazione del piano", "Digita il seriale del SOLO " + label + ":\n" + plan["target"]["serial"])
        if not ok:
            return
        request = {"op": "submit", "id": plan["id"], "approval_digest": plan["approval_digest"],
                   "serial_confirmation": serial.strip(), "backup_confirmed": True, "physical_confirmed": True}

        def submitted(result):
            self.invalidate_plan()
            self.tabs.setCurrentIndex(3)
            self.store.set("last_job", {"host_id": self.snapshot["host_id"], "id": result["id"]})
            self.job_detail.setHtml("<h2>Intervento inviato al nodo</h2><p>" + result["id"] + "</p><p>Il nodo esegue i passi e registra gli esiti. Attendi l'aggiornamento o premi Recupera interventi.</p>")
            QTimer.singleShot(500, self.refresh_jobs)
        self.asynchronous(lambda: self.backend.rpc(request), submitted, "Approvazione e verifica finale sul nodo…")

    def refresh_jobs(self):
        if not self.backend:
            return
        def get():
            jobs = self.backend.rpc({"op": "list_jobs"})
            if isinstance(self.backend, SSHBackend) and self.backend.connection.read_only:
                return jobs
            states = {"MONITORING", "QUEUED", "RUNNING", "WAITING_RESILVER", "BOOT_REVIEW_REQUIRED", "BOOT_TEST_REQUIRED", "ZFS_REVIEW_REQUIRED"}
            return [self.backend.rpc({"op": "status", "id": j["id"]}) if j.get("state") in states or (j.get("role") in {"BOOT", "MISTO"} and j.get("state") == "COMPLETED") else j for j in jobs if j.get("state") not in {"LED", "LED_FINISHED"}]
        self.asynchronous(get, self.render_jobs, "Recupero degli interventi persistenti sul nodo…")

    def render_jobs(self, jobs):
        if self.snapshot and not self.is_demo():
            self.store.set('jobs:' + self.snapshot['host_id'], jobs)
        selected_id = self.jobs[self.job_table.currentRow()]['id'] if 0 <= self.job_table.currentRow() < len(self.jobs) else None
        self.jobs = list(reversed(jobs))
        self.job_table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            for col, val in enumerate((job["id"][:12], job.get("pool", ""), job.get("target", {}).get("serial", ""), job["state"])):
                item = QTableWidgetItem(val)
                if col == 3:
                    item.setForeground(color_for(val))
                self.job_table.setItem(row, col, item)
        if self.jobs:
            index = next((i for i, j in enumerate(self.jobs) if j['id'] == selected_id), -1 if selected_id else 0)
            self.job_table.setCurrentCell(index, 0)
            self.job_selected()
        else:
            self.job_detail.setHtml("<p>Nessun intervento sul nodo. I piani non ancora inviati sono indicati come PREVIEW.</p>")
        self.statusBar().showMessage("Interventi aggiornati")

    def job_selected(self):
        row = self.job_table.currentRow()
        if row < 0 or row >= len(self.jobs):
            return
        job = self.jobs[row]
        percent = job.get("scan_percent")
        self.job_progress.setVisible(percent is not None or job["state"] == "COMPLETED")
        self.job_progress.setValue(100 if job["state"] == "COMPLETED" else min(100, max(0, int(percent or 0))))
        e = html.escape
        body = f"<h2>{e(job.get('pool', 'Intervento'))} · {e(job['state'])}</h2><p>ID {e(job['id'])}</p>"
        if job.get("error"):
            body += "<p style='color:#f18c99'>" + e(job["error"]) + "</p>"
        if job.get("result_message"):
            body += "<p><b>" + e(job["result_message"]) + "</b></p>"
        if job.get("zfs_state"):
            body += "<p>ZFS: " + e(job["zfs_state"]) + " · resilver: " + e(job.get("resilver_state", "DA_VERIFICARE")) + "</p>"
        for disk in job.get("boot_validation", {}).get("disks", []):
            body += "<h4>EFI " + e(disk["serial"] or "membro assente") + "</h4><ul>"
            body += "".join("<li>" + e(name + ": " + c["state"] + " · " + c["detail"]) + "</li>" for name, c in disk["checks"].items()) + "</ul>"
        if job.get("scan"):
            body += "<p>" + e(job["scan"]).replace("\n", "<br>") + "</p>"
            body += '<p>Velocità: ' + e(job.get('scan_rate', 'Non disponibile')) + ' · ETA ZFS: ' + e(job.get('scan_eta', 'Non disponibile')) + '</p>'
        for event in job.get("events", []):
            body += f"<h4>{e(event['state'])}</h4><pre>{e(event['command'])}</pre><pre>{e(event.get('out',''))}</pre><pre>{e(event.get('err',''))}</pre>"
        if job.get("boot_test"):
            body += "<p><b>Test avvio:</b> " + e(job["boot_test"]) + "</p>"
        self.job_detail.setHtml(body)

    def record_boot_result(self):
        row = self.job_table.currentRow()
        if row < 0 or row >= len(self.jobs) or self.is_demo():
            return
        job = self.jobs[row]
        if job.get("role") not in {"BOOT", "MISTO"} or job.get("action") == "offline":
            QMessageBox.information(self, "Avvio autonomo", "Seleziona un intervento di boot o ripristino EFI.")
            return
        serials = [s for s in (job.get("target", {}).get("serial"), (job.get("source") or {}).get("serial")) if s]
        serial, ok = QInputDialog.getItem(self, "Prova fisica", "Quale SOLO disco del mirror è collegato dopo l'avvio di prova?", serials, editable=False)
        if not ok:
            return
        if QMessageBox.question(self, "Registra prova di avvio", "Confermi che il nodo si è appena avviato con il SOLO disco " + serial +
                                " del mirror collegato? Il cambio fisico dei dischi richiede lo spegnimento in laboratorio. "
                                "Prima della prova dell'altro disco ricollega entrambi e attendi il mirror ONLINE e la fine del resilver. "
                                "Il nodo verificherà un nuovo avvio e un solo membro fisico presente; non viene riavviato automaticamente.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        def recorded(result):
            rows = [result if j["id"] == result["id"] else j for j in reversed(self.jobs)]
            self.render_jobs(rows)
        self.asynchronous(lambda: self.backend.rpc({"op": "record_boot_test", "id": job["id"], "serial": serial, "confirmed": True}), recorded,
                          "Verifica dell'avvio fisico dichiarato…")

    def shutdown(self):
        if not self.snapshot or self.is_demo() or self.profile.currentIndex() != 0:
            QMessageBox.information(self, "Spegnimento", "Disponibile solo sul nodo reale con profilo laboratorio.")
            return
        text = ("Comando previsto: shutdown -h +1\n\nSpegne l'host fra un minuto. Prima arresta VM e container dalla GUI Proxmox. "
                "L'helper blocca cluster e carichi ancora accesi. Per annullare sul nodo: shutdown -c.\n\n"
                "Confermi lo spegnimento di " + self.snapshot["hostname"] + "?")
        if QMessageBox.question(self, "Approva spegnimento", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.store.set("maintenance_snapshot", self.snapshot)
            self.asynchronous(lambda: self.backend.rpc({"op": "shutdown", "host_id": self.snapshot["host_id"], "confirmed": True}),
                lambda r: QMessageBox.information(self, "Spegnimento", r["message"]), "Verifica carichi e richiesta spegnimento…")

    def export_report(self):
        if not self.snapshot:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta rapporto anonimizzato", "rapporto-zfs.json", "JSON (*.json);;HTML (*.html);;PDF (*.pdf)")
        if path:
            report = {**self.snapshot, "boot_reports": [boot_report(self.snapshot, p) for p in self.snapshot["pools"] if p.get("role") in {"BOOT", "MISTO"}]}
            report['interventions'] = self.jobs or self.store.get('jobs:' + self.snapshot['host_id'], [])
            rendered = json.dumps(anonymize(report), indent=2, ensure_ascii=False)
            document = '<h1>Rapporto Proxmox ZFS Assistant</h1><p>Dati anonimizzati; avvio fisico separato dallo stato ZFS.</p><pre>' + html.escape(rendered) + '</pre>'
            if Path(path).suffix.lower() == '.pdf':
                printer = QPrinter(QPrinter.PrinterMode.HighResolution)
                printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                printer.setOutputFileName(path)
                text = QTextDocument()
                text.setHtml(document)
                text.print_(printer)
            else:
                Path(path).write_text(document if Path(path).suffix.lower() == '.html' else rendered, encoding='utf-8')
            QMessageBox.information(self, "Rapporto esportato", "Campi identificativi e output grezzi rimossi o mascherati. Rivedi comunque il file prima di pubblicarlo.")

    def _poll(self):
        if self.busy or not self.backend:
            return
        if any(j.get("state") in {"QUEUED", "RUNNING", "MONITORING", "WAITING_RESILVER"} for j in self.jobs):
            self.refresh_jobs()
        elif self.live_refresh.isChecked():
            self.scan()

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "Operazione in corso", "Attendi l'esito della richiesta SSH prima di chiudere questa finestra. Le operazioni già inviate continuano sul nodo.")
            event.ignore()
        else:
            self.poll.stop()
            self.age_timer.stop()
            event.accept()


def main():
    parser = argparse.ArgumentParser(description="Proxmox ZFS Assistant")
    parser.add_argument("--live", action="store_true", help="Apri senza caricare i dati dimostrativi")
    parser.add_argument("--data-dir", help="Directory per database locale e chiavi host")
    parser.add_argument("--screenshot", help="Salva una schermata della demo e chiudi, per verifica del pacchetto")
    args = parser.parse_args()
    directory = args.data_dir or str(Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "ProxmoxZfsAssistant")
    app = QApplication(sys.argv[:1])
    # Some headless Windows environments cannot enumerate system fonts. Read the
    # installed font directly; never redistribute Microsoft's font files.
    if not QFontDatabase.families() and os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf", "segoeuil.ttf"):
            if (fonts / filename).exists():
                QFontDatabase.addApplicationFont(str(fonts / filename))
    app.setFont(QFont("Segoe UI", 10))
    app.setApplicationName("Proxmox ZFS Assistant")
    app.setStyleSheet(STYLE)
    window = Window(directory, demo=bool(args.screenshot) or not args.live)
    window.show()
    if args.screenshot:
        attempts = [0]
        timer = QTimer(window)
        def capture():
            attempts[0] += 1
            if window.snapshot and not window.busy:
                output = Path(args.screenshot)
                output.parent.mkdir(parents=True, exist_ok=True)
                app.exit(0 if window.grab().save(str(output)) else 2)
            elif attempts[0] > 100:
                app.exit(3)
        timer.timeout.connect(capture)
        timer.start(100)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
