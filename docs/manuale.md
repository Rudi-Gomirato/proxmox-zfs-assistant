# Manuale operativo

## Avviare e provare l'app

Su Windows apri `avvia.cmd` nella cartella del progetto. L'app parte con dati dimostrativi: nessuna connessione SSH e nessuna scrittura sui dischi. Dalla Panoramica puoi scegliere la demo con pool VM mirror oppure RAIDZ1.

Il pacchetto compilato si avvia da `ProxmoxZfsAssistant.exe`. Mantieni l'intera cartella della distribuzione, inclusa `_internal` e le licenze; l'EXE da solo non è sufficiente.

Per installare da sorgente occorre Python 3.10 o successivo. Esegui `installa.cmd`, poi `avvia.cmd`. Per sviluppare:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pve_disk_assistant
```

## Primo collegamento reale

1. Apri Connessione e inserisci indirizzo, porta, utente e chiave SSH o password. Le credenziali rimangono in memoria e non vengono salvate nel database.
2. Per l'installazione iniziale usa una sessione root già autorizzata. Premi Installa / aggiorna helper sul nodo e confronta l'impronta SSH con quella del server prima di memorizzarla.
3. Premi Collega e analizza. L'helper legge inventario, topologia, storage, configurazioni locali VM/CT, SMART e metodo di boot.
4. In Dischi registra posizione o etichetta di ogni dispositivo mentre tutti i dischi sono ancora presenti. Lo storico permette di recuperare associazioni dopo una scomparsa, se il percorso precedente è disponibile.

L'helper usa soltanto la libreria standard Python. Il nodo deve già disporre degli strumenti necessari: `zpool`, `lsblk`, `findmnt`, `wipefs`, `pveversion`; per SMART `smartctl`; per copiare GPT `sfdisk`, `sgdisk`, `partprobe`, `udevadm`; per il boot `proxmox-boot-tool`. L'app non installa automaticamente pacchetti e non modifica le impostazioni SSH del server.

L'installazione crea `/usr/local/lib/pve-disk-assistant/helper.pyz`. I piani e gli esiti sono salvati in `/var/lib/pve-disk-assistant`, accessibile a root. Aggiorna l'helper dopo aver aggiornato l'app, a interventi conclusi.

## Accesso con utente non root

Un amministratore può installare prima l'helper e autorizzare un utente SSH ad avviarlo tramite una regola sudo specifica, senza autorizzare una shell generica. Esempio da adattare con `visudo`:

```text
operatore ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/lib/pve-disk-assistant/helper.pyz
```

Poi seleziona Usa sudo -n limitato all'helper già installato. Cartella, archivio e componenti devono appartenere a root e non essere modificabili dall'operatore. La regola concede tutte le operazioni esposte dall'helper, comprese le operazioni approvate sui dischi: riservala a operatori autorizzati. In questa prima versione anche la raccolta diagnostica usa l'helper con questi privilegi; non è implementata una separazione di ruoli nell'app.

## Interpretare la topologia

Il tipo RAID deriva dall'albero ZFS, non dal nome del pool o dal numero di dischi ancora visibili. Vengono distinti mirror, RAIDZ1/2/3, dischi singoli e classi ausiliarie. Un membro assente resta nell'albero con il GUID, quando disponibile.

Il ruolo boot deriva dal filesystem root, dalle dipendenze di avvio e dalle partizioni osservate. Gli storage ZFS di Proxmox associano il pool ai carichi VM/CT del nodo. Un pool può essere MISTO, con sistema e VM insieme.

Se la topologia non è decodificabile, l'app chiede quale configurazione ti aspetti. La dichiarazione viene registrata ma non abilita scritture: correggi il problema di raccolta o fai verificare l'output da un tecnico. L'export anonimizzato aiuta a segnalare problemi di parsing; non è un file da reimportare per eseguire operazioni.

## Sostituzione nel laboratorio senza hot swap

1. Seleziona il pool e il membro problematico. Apri Istruzioni per sostituzione fisica e annota seriale, posizione e GUID.
2. Arresta VM e container ordinatamente dalla GUI Proxmox. L'app non gestisce lo spegnimento individuale dei carichi.
3. Puoi usare Spegni il nodo di laboratorio: mostra `shutdown -h +1`, controlla che non ci siano carichi accesi, configurazione cluster o interventi attivi e chiede approvazione. Puoi anche spegnere dalla GUI Proxmox. Per annullare una richiesta pianificata sul nodo: `shutdown -c`.
4. Verifica fisicamente che il PC sia spento. La perdita di SSH non è sufficiente. Sostituisci il solo disco identificato e accendi il PC manualmente.
5. Aggiorna l'analisi quando il nodo torna disponibile. Il GUID del vecchio membro deve restare riconoscibile nel pool; il nuovo seriale deve comparire come ricambio libero.
6. Seleziona il ricambio e genera il piano. Un disco con firme o partizioni già presenti viene rifiutato: la preparazione o cancellazione di dischi usati è una procedura separata, non automatizzata.
7. Leggi comandi e spiegazioni, conferma backup e identificazione fisica, quindi approva digitando il seriale del nuovo disco.

L'app resta sul PC Windows durante il fermo e conserva le osservazioni precedenti. Non modificare i cablaggi degli altri dischi durante la sostituzione.

## Sostituzione a caldo in produzione

Seleziona il profilo Produzione e conferma di aver verificato il supporto hot swap sul modello e sul vano specifici. Hot swap e controllo LED sono capacità distinte.

Se il dispositivo problematico è ancora presente e risulta FAULTED o DEGRADED, usa Prepara rimozione a caldo: mostra e fa approvare `zpool offline` per il suo GUID, dopo aver controllato gli altri membri. Attendi READY_FOR_REMOVAL nel pannello Interventi e segui la procedura fisica del produttore. Il comando offline non spegne elettricamente il vano. Per un membro già assente o escluso non serve ripetere il comando.

Dopo l'inserimento aggiorna l'analisi, seleziona il nuovo seriale e genera il piano di sostituzione. Non usare detach, add o comandi forzati per aggirare un blocco. Non eseguire questa prima versione su dati operativi prima dei collaudi descritti nella matrice di compatibilità.

## Boot e dati VM

Per i pool VM l'app usa `zpool replace`. Se il membro è una partizione in un layout GPT dati ZFS riconosciuto, replica solo la tabella partizioni dal riferimento sano e rigenera i GUID. Non copia filesystem e non inizializza ESP o bootloader.

Per il boot sono supportati root ZFS in mirror di due membri, GPT con ESP e partizione ZFS e gestione riconoscibile tramite `proxmox-boot-tool`. Il piano copia la GPT nella direzione sano → nuovo, rigenera i GUID, sostituisce la partizione ZFS e prepara la sola ESP nuova nella modalità GRUB o systemd-boot rilevata. Il GRUB legacy e i layout personalizzati restano bloccati.

Il resilver completato non prova che il firmware possa avviare il nuovo SSD. Il test di boot rimane un'attività separata in manutenzione, con esito indicato come da verificare.

## LED e identificazione fisica

Questa implementazione comanda soltanto LED locate esposti da un enclosure Linux e associati al block device tramite sysfs. Non usa letture artificiali per far lampeggiare LED di attività. SATA diretto può non offrire alcun LED controllabile: usa le etichette e il seriale.

L'identificazione dura 30 secondi, con ripristino dello stato precedente tramite un worker sul nodo. Non viene modificato l'indicatore fault. Un riavvio o guasto dell'host durante il timer può impedire il ripristino: verificarlo sul server. Al primo uso collauda visivamente l'associazione tra seriale e vano.

## Interventi e interruzioni

La sostituzione viene registrata prima dell'esecuzione. Un worker separato sul nodo continua anche se si chiude il collegamento SSH; dopo il ritorno usa Recupera interventi dal nodo. Il resilver è svolto da ZFS, non da Windows.

- PREVIEW: anteprima non eseguita; scade dopo dieci minuti.
- QUEUED / RUNNING: piano approvato in coda o in esecuzione.
- READY_FOR_REMOVAL: offline del membro problematico confermato; seguire la procedura hardware.
- MONITORING: comandi conclusi, ricostruzione da controllare.
- COMPLETED: nuovo membro nel pool e controlli finali ZFS soddisfatti; il boot resta separato.
- BLOCKED / FAILED / UNKNOWN: non ripetere alla cieca. Il dettaglio contiene i passi iniziati e gli esiti disponibili.

UNKNOWN mantiene il blocco per ulteriori invii. Non esiste un pulsante per ripetere automaticamente una GPT, una formattazione o un comando con esito ignoto. Un amministratore deve riconciliare la situazione sul nodo e il registro prima di pianificare un nuovo intervento; conservare il vecchio record come evidenza e archiviarlo manualmente fuori dalla directory attiva se necessario.

## Dati locali

Database, posizioni e chiavi host sono in `%LOCALAPPDATA%\ProxmoxZfsAssistant`. Per usare una cartella diversa passa `--data-dir`. I rapporti pubblici mascherano le stringhe identificative e omettono output grezzi; lo storico locale e i registri sul nodo restano privati e contengono identità reali.

La raccolta periodica è opzionale. I dati vecchi o una lettura fallita vengono segnalati come non aggiornati. L'app non può recuperare via SSH un sistema che non si avvia.
