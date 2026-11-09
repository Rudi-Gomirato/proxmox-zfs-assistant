# Manuale operativo

Aggiornamento 0.3.0: [comportamento attuale, estensioni e collaudo](collaudo-0.3.md). Le limitazioni precedenti descritte sotto sono aggiornate da questa nota.
## Avviare e provare l'app

Su Windows apri `avvia.cmd` nella cartella del progetto. L'app parte con dati dimostrativi: nessuna connessione SSH e nessuna scrittura sui dischi. Dalla Panoramica puoi scegliere VM mirror, VM RAIDZ1 oppure EFI non valida (root mirror ONLINE con una ESP non riconosciuta).

Il pacchetto compilato si avvia da `ProxmoxZfsAssistant.exe`. Mantieni l'intera cartella della distribuzione, inclusa `_internal` e le licenze; l'EXE da solo non è sufficiente.

Per installare da sorgente occorre Python 3.10 o successivo. Esegui `installa.cmd`, poi `avvia.cmd`. Per sviluppare:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pve_disk_assistant
```

## Primo collegamento reale

1. Apri Connessione e inserisci indirizzo, porta, utente e chiave SSH o password. Il campo Chiave privata può restare vuoto se usi password o agente SSH. Le credenziali rimangono in memoria e non vengono salvate nel database.
2. Per l'installazione iniziale usa una sessione root già autorizzata. Premi Installa / aggiorna helper sul nodo e confronta l'impronta SSH con quella del server prima di memorizzarla.
3. Premi Collega e analizza. L'helper legge inventario, topologia, storage, configurazioni locali VM/CT, SMART e metodo di boot.
4. In Dischi registra posizione o etichetta di ogni dispositivo mentre tutti i dischi sono ancora presenti. Lo storico permette di recuperare associazioni dopo una scomparsa, se il percorso precedente è disponibile.

L'helper usa soltanto la libreria standard Python. Il nodo deve già disporre degli strumenti necessari: `zpool`, `lsblk`, `findmnt`, `wipefs`, `pveversion`; per SMART `smartctl`; per copiare GPT `sfdisk`, `sgdisk`, `partprobe`, `udevadm`; per il boot `proxmox-boot-tool`, `blkid`, `fsck.fat`, `mount`, `umount`, `unshare` ed `efibootmgr`. L'app non installa automaticamente pacchetti e non modifica le impostazioni SSH del server.

L'installazione crea `/usr/local/lib/pve-disk-assistant/helper.pyz`. I piani e gli esiti sono salvati in `/var/lib/pve-disk-assistant`, accessibile a root. Aggiorna l'helper dopo aver aggiornato l'app, a interventi conclusi.

La versione 0.2.0 richiede l'helper 0.2.0 per piani e interventi. Un helper precedente consente ancora la raccolta, segnalata come incompatibile, ma non abilita le nuove operazioni. Aggiorna dalla scheda Connessione usando la sessione root e ripeti l'analisi. I vecchi interventi boot marcati COMPLETED vengono rivalutati con i nuovi controlli: la vecchia etichetta non dimostra che entrambi i dischi fossero avviabili.

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

Per il boot sono supportati root ZFS in mirror di due membri, UEFI x86_64, GPT con ESP e partizione ZFS e gestione riconoscibile tramite `proxmox-boot-tool`. Anche la configurazione EFI del membro di riferimento deve essere verificata. Il piano copia la GPT nella direzione sano → nuovo, rigenera i GUID e sostituisce la partizione ZFS. Attende poi il resilver, ricontrolla disco e ESP destinataria e prepara la sola ESP nuova nella modalità GRUB o systemd-boot rilevata. Esegue infine `proxmox-boot-tool refresh`, che sincronizza tutte le ESP registrate, e rilegge lo stato. Il GRUB legacy e i layout personalizzati restano bloccati.

La ripresa dopo il resilver avviene interrogando l'intervento dall'app e usa il cursore salvato sul nodo: non ripete GPT o replace. Se chiudi l'app nella fase WAITING_RESILVER, ZFS continua a ricostruire; riapri l'app e recupera gli interventi per avviare i passi EFI già approvati. Un esito ignoto non viene ritentato automaticamente.

## Diagnostica EFI e ripristino della sola ESP

La scheda **Avvio EFI** controlla ogni disco del mirror, incluso il membro rimasto originale. Lo stato ONLINE di ZFS indica lo stato dei dati, non quello dell'avvio. L'app confronta partizione GPT EFI e partizione ZFS, UUID e PARTUUID, FAT32, esito di `fsck.fat -n`, registro `/etc/kernel/proxmox-boot-uuids`, stato di proxmox-boot-tool, loader EFI, configurazione e coppie kernel/initrd. Per il percorso del firmware considera le voci attive di `efibootmgr -v` e il fallback `EFI/BOOT/BOOTX64.EFI` del disco.

Le letture dirette usano `blkid -p` e `wipefs --json`. L'ispezione dei file usa un mount FAT in sola lettura con `ro,nodev,nosuid,noexec`, dentro una namespace privata creata con `unshare --mount --propagation private`; il mount viene rimosso al termine. La diagnosi non formatta, non ripara FAT e non cambia NVRAM. Una ESP già montata non viene smontata dall'app: il controllo resta incompleto. Se mancano strumenti o prove, l'esito è da verificare.

Se il mirror è sano ma una ESP è incompleta, seleziona la riga e premi **Piano ripristino della sola ESP selezionata**. È richiesto un altro membro con configurazione EFI verificata. Il piano mostra comandi e spiegazioni e richiede approvazione con il seriale. Non contiene copia GPT, `zpool replace` o `grub-install` generico:

- Una FAT32 già valida e coerente non viene formattata; viene reinizializzato il loader nella modalità del riferimento.
- La formattazione è proposta soltanto se il probe diretto non rileva un filesystem e wipefs conferma l'assenza di firme, sulla sola partizione GPT EFI identificata. Una firma sconosciuta o una FAT danneggiata blocca il piano.
- `init` riguarda la ESP selezionata; il successivo `refresh` sincronizza tutte le ESP registrate. I controlli finali riguardano nuovamente entrambi i dischi.

Il piano conserva le evidenze precedenti e successive. FSTYPE vuoto non dimostra da solo una cancellazione né identifica la causa del problema. Gli UUID obsoleti nel registro e gli avvisi di proxmox-boot-tool sono segnalati, senza pulizia automatica. Richiedono verifica amministrativa prima del completamento.

La verifica PE/COFF dei file EFI controlla la struttura x64, non le firme crittografiche, la catena Secure Boot o il funzionamento del firmware. systemd-boot con Secure Boot attivo non è certificabile con questa procedura. Anche una configurazione EFI corretta rimane **avvio autonomo NON TESTATO**.

## Prove fisiche di avvio dei due dischi

Esegui queste prove in manutenzione con backup verificati e carichi arrestati. L'app non riavvia il server per provarne il boot. Se un disco non parte, conserva le evidenze e usa una procedura di recupero; l'app non può raggiungerlo via SSH.

1. Attendi mirror ONLINE, nessun resilver attivo e configurazione EFI verificata su entrambi i membri. Annota seriali e cablaggio.
2. Spegni il nodo. Nel laboratorio senza hot swap scollega il solo altro membro del mirror di boot e avvia con il disco da provare. Non eseguire detach e non ricreare il pool.
3. Ricollega l'app via SSH. In Interventi scegli il lavoro e premi **Registra prova di avvio autonomo**, indicando il seriale presente. L'helper richiede un avvio successivo al piano, un solo disco del mirror fisicamente presente e BootCurrent associato alla sua ESP. La prova resta dichiarata dall'operatore. Se il firmware non espone questa evidenza, serve verifica manuale e l'app non conclude il lavoro.
4. Spegni e ricollega entrambi i dischi. Riavvia e attendi nuovamente mirror ONLINE e fine del resilver **prima di isolare l'altro membro**. Non alternare avvii isolati su copie non risincronizzate: anche il sistema operativo scrive sul pool durante un avvio.
5. Ripeti la prova sull'altro disco, in un nuovo avvio, poi ripristina entrambi e attendi ancora il mirror sano. L'intervento può diventare COMPLETED solo con le due prove registrate, evidenze EFI ancora coerenti e ZFS sano. Modifiche ai file EFI o alla configurazione invalidano le prove precedenti.

Riferimenti delle procedure: [avvio di Proxmox](https://pve.proxmox.com/pve-docs/pve-admin-guide.html#sysboot) e [ZFS su Proxmox](https://pve.proxmox.com/pve-docs/pve-admin-guide.html#chapter_zfs). Le verifiche automatiche non sostituiscono il collaudo sul modello di server.

## LED e identificazione fisica

Questa implementazione comanda soltanto LED locate esposti da un enclosure Linux e associati al block device tramite sysfs. Non usa letture artificiali per far lampeggiare LED di attività. SATA diretto può non offrire alcun LED controllabile: usa le etichette e il seriale.

L'identificazione dura 30 secondi, con ripristino dello stato precedente tramite un worker sul nodo. Non viene modificato l'indicatore fault. Un riavvio o guasto dell'host durante il timer può impedire il ripristino: verificarlo sul server. Al primo uso collauda visivamente l'associazione tra seriale e vano.

## Interventi e interruzioni

La sostituzione viene registrata prima dell'esecuzione. Un worker separato sul nodo continua anche se si chiude il collegamento SSH; dopo il ritorno usa Recupera interventi dal nodo. Il resilver è svolto da ZFS, non da Windows.

- PREVIEW: anteprima non eseguita; scade dopo dieci minuti.
- QUEUED / RUNNING: piano approvato in coda o in esecuzione.
- READY_FOR_REMOVAL: offline del membro problematico confermato; seguire la procedura hardware.
- MONITORING: comandi conclusi, ricostruzione da controllare.
- WAITING_RESILVER: replace boot concluso, passi EFI sospesi finché il mirror non è ricostruito.
- ZFS_REVIEW_REQUIRED: pool o membro non soddisfano i controlli conclusivi e non risulta una ricostruzione attiva.
- BOOT_REVIEW_REQUIRED: ZFS ricostruito, ma configurazione EFI o registro richiedono verifica.
- BOOT_TEST_REQUIRED: ZFS e configurazione EFI verificati; mancano le prove fisiche distinte dei due membri.
- COMPLETED: controlli ZFS soddisfatti; per il boot anche EFI di entrambi e prove di avvio autonomo registrate e ancora coerenti.
- BLOCKED / FAILED / UNKNOWN: non ripetere alla cieca. Il dettaglio contiene i passi iniziati e gli esiti disponibili.

UNKNOWN mantiene il blocco per ulteriori invii. Non esiste un pulsante per ripetere automaticamente una GPT, una formattazione o un comando con esito ignoto. Un amministratore deve riconciliare la situazione sul nodo e il registro prima di pianificare un nuovo intervento; conservare il vecchio record come evidenza e archiviarlo manualmente fuori dalla directory attiva se necessario.

## Dati locali

Database, posizioni e chiavi host sono in `%LOCALAPPDATA%\ProxmoxZfsAssistant`. Per usare una cartella diversa passa `--data-dir`. I rapporti pubblici mascherano le stringhe identificative e omettono output grezzi; lo storico locale e i registri sul nodo restano privati e contengono identità reali.

Se l'app non si apre con `avvia.cmd` o con l'EXE, un messaggio indica il log `startup-error.log` nella stessa directory dati. Da sorgente esegui nuovamente `installa.cmd` con l'interprete scelto. La CI prova Python 3.11 e 3.12; un'installazione locale richiede comunque tutte le dipendenze Qt indicate nel progetto.

La raccolta periodica è opzionale. I dati vecchi o una lettura fallita vengono segnalati come non aggiornati. L'app non può recuperare via SSH un sistema che non si avvia.
