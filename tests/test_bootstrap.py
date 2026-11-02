import sys

import pytest

from pve_disk_assistant import bootstrap


def test_pythonw_startup_error_is_recorded_in_requested_private_directory(monkeypatch, tmp_path):
    def failed():
        raise ImportError("Synthetic missing GUI dependency")
    monkeypatch.setattr(bootstrap, "launch_gui", failed)
    monkeypatch.setattr(sys, "argv", ["app", "--data-dir", str(tmp_path)])
    monkeypatch.setenv("PVE_ASSISTANT_NO_ERROR_DIALOG", "1")
    with pytest.raises(SystemExit) as error:
        bootstrap.main()
    assert error.value.code == 1
    assert "Synthetic missing GUI dependency" in (tmp_path / "startup-error.log").read_text()


def test_normal_gui_exit_is_not_logged_as_an_error(monkeypatch, tmp_path):
    def normal():
        raise SystemExit(0)
    monkeypatch.setattr(bootstrap, "launch_gui", normal)
    monkeypatch.setattr(sys, "argv", ["app", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        bootstrap.main()
    assert error.value.code == 0
    assert not (tmp_path / "startup-error.log").exists()
