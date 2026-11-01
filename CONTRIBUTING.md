# Contribuire

Installa Python 3.10 o successivo, crea un ambiente virtuale e installa `requirements-dev.txt`. Esegui `python -m pytest -q` prima di inviare una modifica. I test usano dati sintetici e non devono contattare host Proxmox reali.

Ogni nuovo layout ZFS o backend LED deve avere fixture anonimizzate, test delle condizioni di blocco e una descrizione del collaudo hardware. Non introdurre comandi arbitrari, concatenazioni di shell, opzioni di forzatura o retry di operazioni con esito incerto. I controlli operativi appartengono al nodo, non soltanto alla GUI.

Non allegare credenziali, inventari grezzi, indirizzi o seriali reali alle issue. Un report esportato va comunque rivisto prima di pubblicarlo. Le prove distruttive devono essere esplicitamente separate dalla CI e svolte soltanto con dati sacrificabili.

Per le modifiche alla GUI verifica almeno i profili demo mirror e RAIDZ, la lettura dei seriali, il blocco dell'esecuzione demo e l'invalidazione del piano quando cambia una selezione.
