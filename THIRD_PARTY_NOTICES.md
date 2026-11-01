# Componenti di terze parti

Il codice originale del progetto è distribuito sotto licenza MIT. Le dipendenze conservano le rispettive licenze e non sono ricoperte dalla licenza MIT del progetto.

- PySide6 Essentials e shiboken6: progetto Qt for Python, con le condizioni applicabili della distribuzione Qt/PySide. Fonti e informazioni: https://code.qt.io/pyside/pyside-setup.git/ e https://doc.qt.io/qtforpython-6/licenses.html.
- Qt: https://code.qt.io/qt/ e https://www.qt.io/licensing/.
- Paramiko: https://github.com/paramiko/paramiko.
- cryptography: https://github.com/pyca/cryptography.
- bcrypt: https://github.com/pyca/bcrypt.
- PyNaCl: https://github.com/pyca/pynacl; include componenti libsodium.
- CFFI: https://foss.heptapod.net/pypy/cffi.
- pycparser: https://github.com/eliben/pycparser.
- Invoke: https://github.com/pyinvoke/invoke.
- Python: https://www.python.org/ e https://docs.python.org/3/license.html.
- PyInstaller, usato per il packaging: https://pyinstaller.org/en/stable/license.html.

Il build Windows include una directory `licenses` con le licenze presenti nelle distribuzioni installate. Il programma viene distribuito in modalità directory, mantenendo le librerie Qt separate e sostituibili. Non distribuire soltanto il file EXE: conservare l'intera cartella, le licenze, gli avvisi e le istruzioni. Le sorgenti di questa applicazione e lo script di build consentono di ricostruire l'app con altre versioni compatibili delle librerie.

Riferimento funzionale e visivo: jt-proxense di Jason Cheng, https://github.com/jasoncheng7115/jt-proxense, che dichiara AGPL-3.0-or-later. Nessun codice, asset o schermata di quel progetto è incorporato in questa implementazione.
