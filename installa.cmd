@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
py -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
  goto dependencies
)
python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
if errorlevel 1 (
  echo Python 3.10 o successivo non trovato. Installa Python e abilita python nel PATH.
  goto failed
)
python -m venv .venv
if errorlevel 1 goto failed
:dependencies
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3,10))"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Installazione completata. Avvia l'app con avvia.cmd.
pause
exit /b 0
:failed
echo Installazione non completata. Controlla Python, connessione e messaggi precedenti.
pause
exit /b 1
