import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from pve_disk_assistant.demo import DemoBackend, snapshot
from pve_disk_assistant.gui import STYLE, Window


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setStyleSheet(STYLE)
    return application


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
