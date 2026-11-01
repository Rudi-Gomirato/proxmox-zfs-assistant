@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Prima esegui installa.cmd per installare Python e le dipendenze del progetto.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0src"
start "Proxmox ZFS Assistant" ".venv\Scripts\pythonw.exe" -m pve_disk_assistant
