import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pve_disk_assistant.demo import DemoBackend, snapshot
from pve_disk_assistant.gui import STYLE, Window
from copy import deepcopy


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(STYLE)
    yield application
    # Destroy C++ widgets while Python callbacks and QApplication still exist,
    # instead of leaving their reference cycles to interpreter finalization.
    import gc
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    for widget in application.topLevelWidgets():
        if shiboken6.isValid(widget):
            shiboken6.delete(widget)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def test_gui_renders_both_topologies_and_preserves_approval_boundary(app, tmp_path):
    window = Window(tmp_path, demo=False)
    window.backend = DemoBackend()
    window.render_snapshot(snapshot())
    assert window.tree.topLevelItemCount() == 2
    assert window.disk_table.rowCount() == 4
    assert not window.execute_button.isEnabled()
    window.backend = DemoBackend(True)
    window.render_snapshot(snapshot(True))
    assert window.disk_table.rowCount() == 5
    assert window.tree.topLevelItem(1).child(0).childCount() == 3
    assert "RAIDZ1" in window.pool_combo.itemText(1)
    window.close()


def test_changing_target_or_profile_invalidates_plan(app, tmp_path):
    window = Window(tmp_path, demo=False)
    window.backend = DemoBackend()
    window.render_snapshot(snapshot())
    window.plan = {"blocks": []}
    window.profile.setCurrentIndex(1)
    assert window.plan is None
    assert window.hot_swap.isEnabled()
    assert not window.execute_button.isEnabled()
    window.close()


def test_online_pool_with_bad_esp_is_visible_in_efi_diagnostics(app, tmp_path):
    window = Window(tmp_path, demo=False)
    window.backend = DemoBackend()
    window.render_snapshot(snapshot())
    assert "fat32 · OK" in window.boot_detail.toPlainText()
    window.backend = DemoBackend(boot_fault=True)
    window.render_snapshot(snapshot(boot_fault=True))
    assert window.snapshot["pools"][0]["state"] == "ONLINE"
    assert window.boot_table.rowCount() == 2
    assert window.boot_table.item(0, 7).text() == "ERROR"
    assert window.card_values["boot"].text() == "DA VERIFICARE"
    assert "NON TESTATO" in window.boot_detail.toPlainText()
    assert "fat32 · ERROR" in window.boot_detail.toPlainText()
    assert "loader: unknown" in window.boot_detail.toPlainText()
    window.close()


def test_job_detail_refreshes_when_selected_job_changes_state(app, tmp_path):
    window = Window(tmp_path, demo=False)
    job = {"id": "synthetic-job", "pool": "rpool", "state": "MONITORING"}
    window.render_jobs([job])
    assert "MONITORING" in window.job_detail.toPlainText()
    window.render_jobs([{**job, "state": "BOOT_REVIEW_REQUIRED", "result_message": "EFI da verificare"}])
    assert "BOOT_REVIEW_REQUIRED" in window.job_detail.toPlainText()
    assert "EFI da verificare" in window.job_detail.toPlainText()
    window.close()


def test_100_refreshes_preserve_pool_and_tab_and_missing_pool_never_redirects(app, tmp_path):
    window = Window(tmp_path, demo=False)
    window.backend = DemoBackend()
    snap = snapshot()
    window.render_snapshot(snap)
    window.pool_combo.setCurrentIndex(window.pool_combo.findData('2000'))
    window.tabs.setCurrentIndex(2)
    window.disk_table.selectRow(2)
    serial = window.snapshot['disks'][2]['serial']
    for _ in range(100):
        changed = deepcopy(snap)
        changed['pools'].reverse()
        changed['disks'].reverse()
        window.render_snapshot(changed)
        assert window.pool_combo.currentData() == '2000'
        assert window.tabs.currentIndex() == 2
        assert window.selected_disk()['serial'] == serial
    changed['pools'] = [p for p in changed['pools'] if p['guid'] != '2000']
    window.render_snapshot(changed)
    assert window.pool_combo.currentIndex() == -1
    assert window.plan is None
    assert 'non più disponibile' in window.issues.text()
    window.close()


def test_older_async_snapshot_is_ignored(app, tmp_path):
    window = Window(tmp_path, demo=False)
    window.backend = DemoBackend()
    fresh = snapshot()
    fresh['collected_at'] = '2030-01-02T00:00:00+00:00'
    window.render_snapshot(fresh)
    window.pool_combo.setCurrentIndex(window.pool_combo.findData('2000'))
    stale = deepcopy(fresh)
    stale['collected_at'] = '2030-01-01T00:00:00+00:00'
    stale['pools'] = []
    window.render_snapshot(stale)
    assert window.snapshot is fresh
    assert window.pool_combo.currentData() == '2000'
    window.close()


def test_refresh_preserves_approved_plan_until_safety_data_changes(app, tmp_path):
    from pve_disk_assistant.core import plan_replacement
    window = Window(tmp_path, demo=False)
    window.backend = DemoBackend()
    snap = snapshot()
    window.render_snapshot(snap)
    window.pool_combo.setCurrentIndex(window.pool_combo.findData('2000'))
    plan = plan_replacement(snap, {'pool_guid': '2000', 'old_guid': '202', 'target': '/dev/disk/by-id/ata-DEMO_NEW'})
    window.render_plan(plan)
    window.backup.setChecked(True)
    window.render_snapshot(deepcopy(snap))
    assert window.plan == plan and window.backup.isChecked()
    changed = deepcopy(snap)
    changed['disks'][-1]['serial'] = 'CHANGED'
    window.render_snapshot(changed)
    assert window.plan is None and not window.execute_button.isEnabled()
    window.close()


@pytest.mark.parametrize('extension', ['json', 'html', 'pdf'])
def test_anonymous_report_formats(app, tmp_path, monkeypatch, extension):
    from pve_disk_assistant import gui
    window = Window(tmp_path, demo=False)
    window.backend = DemoBackend()
    window.render_snapshot(snapshot())
    path = tmp_path / ('report.' + extension)
    monkeypatch.setattr(gui.QFileDialog, 'getSaveFileName', lambda *a: (str(path), ''))
    monkeypatch.setattr(gui.QMessageBox, 'information', lambda *a: None)
    window.export_report()
    assert path.stat().st_size > 100
    if extension == 'pdf':
        assert path.read_bytes().startswith(b'%PDF')
    else:
        assert 'DEMO_BOOT_A' not in path.read_text(encoding='utf-8')
    window.close()


def test_saved_profiles_exclude_password(app, tmp_path, monkeypatch):
    from pve_disk_assistant import gui
    window = Window(tmp_path, demo=False)
    window.host.setText('example.invalid')
    window.password.setText('never-save-this')
    monkeypatch.setattr(gui.QInputDialog, 'getText', lambda *a: ('Test / lab', True))
    window.save_profile()
    assert 'never-save-this' not in str(window.store.get('profiles'))
    window.load_profile()
    assert window.password.text() == ''
    assert window.backend is None
    window.close()
