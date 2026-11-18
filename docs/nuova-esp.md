# Inizializza nuova ESP dopo sostituzione disco — 0.5.2

Questa procedura completa dall'app la fase EFI di una sostituzione del mirror root ZFS. È distinta dal **Piano ripristino della sola ESP selezionata**, che richiede una FAT32 già coerente e non propone mai una formattazione.

## Aggiornamento e recupero di una sostituzione precedente

Aggiornare il client Windows e, dalla scheda **Connessione**, usare **Installa / aggiorna helper sul nodo**. Non aggiornare durante comandi attivi. Conservare il registro `/var/lib/pve-disk-assistant`: serve a collegare la nuova ESP alla sostituzione già eseguita. L'aggiornamento non ricrea GPT e non ripete `zpool replace`.

1. Collegarsi al nodo, aggiornare l'analisi e recuperare gli interventi.
2. In **Interventi**, selezionare la sostituzione di boot e premere **Prosegui con nuova ESP**. In alternativa, in **Avvio EFI** selezionare il nuovo disco e premere **Inizializza nuova ESP dopo sostituzione disco**. Il secondo percorso richiede un unico intervento precedente corrispondente.
3. Leggere il piano: seriale, WWN, percorso persistente, PARTUUID, intervento ZFS originario, numero di byte verificati e comandi spiegati.
4. Confermare backup/manutenzione e identificazione fisica, digitare il seriale e approvare la finestra dedicata alla **formattazione della sola ESP**, che mostra nuovamente WWN e PARTUUID. La risposta predefinita è No.
5. Seguire il nuovo intervento EFI. L'intervento ZFS conserva il collegamento al suo ID. Non occorre inserire comandi SSH manuali per il percorso supportato.

## Verifiche prima della formattazione

- Root ZFS, Proxmox 8/9, UEFI x86_64, mirror di due membri ONLINE, contatori senza errori e nessun resilver attivo. Deve risultare un resilver concluso senza errori (o uno scrub successivo concluso), diverso dal risultato precedente alla sostituzione.
- Identità del ricambio per by-id, seriale univoco e WWN normalizzato; un WWN condiviso richiede anche un alias by-id indipendente contenente il seriale, univoco nell'inventario e verificato sullo stesso dispositivo in esecuzione. WWN assente o identità ambigua restano bloccanti. PARTUUID dell'ESP corrispondente a quello registrato **prima** del comando di sostituzione. Nel registro del nodo deve esserci un `replace` terminato con successo sullo stesso host e pool.
- ESP GPT unica, non montata, sul nuovo disco, distinta dalla partizione ZFS e senza dispositivi dipendenti. Il membro di riferimento deve avere una configurazione di avvio verificata.
- `blkid -p` e `wipefs --json` devono escludere firme. Un risultato `blkid` contenente solo metadati GPT `PART_ENTRY_*` è distinto da un filesystem: il solo codice di uscita non basta.
- Nel percorso standard, tutti i byte della ESP devono essere nulli. La scansione si ferma al primo blocco contenente dati residui. L’anteprima mostra i byte esaminati, i byte non nulli trovati nella porzione letta, il primo offset e il motivo dell’arresto. Limiti per lettura: 4 GiB e 60 secondi.

I controlli vengono ripetuti all'approvazione, all'avvio del worker e subito prima del format. Durante la manutenzione lasciare i dischi in gestione al solo intervento approvato. Il comando Proxmox `init` richiama l’installer del bootloader, che può aggiornare il firmware. Prima dell’esecuzione la GUI richiede di autorizzare anche questo effetto. Lo storico confronta BootOrder con l’anteprima. Il piano separato per registrare una voce mancante usa invece `--create-only`.

## Quando il piano segnala dati residui

1. Confrontare seriale, modello, percorso by-id, PARTUUID, GUID ZFS e disco di riferimento. I byte non riconosciuti possono avere valore. Se occorre conservarli, chiudere il piano prima di approvare.
2. Quando compare **Inizializzazione avanzata ESP**, aprirla. Il nodo crea un nuovo piano e legge l’intera partizione. L’impronta SHA256 vincola l’approvazione al contenuto esaminato. Le letture incomplete o fallite richiedono una nuova diagnosi.
3. Verificare nell’anteprima la dimensione della ESP e la partizione ZFS esclusa dal format. Il nodo controlla mount nei namespace dei processi, descrittori aperti, dispositivi dipendenti, swap e configurazioni persistenti. BootCurrent deve indicare una voce coerente con la ESP del disco di riferimento.
4. Confermare backup, manutenzione, effetti del bootloader sul firmware, perdita dei dati della sola ESP e identità del ricambio. Digitare il seriale esattamente come rilevato. Premere **Approva piano EFI** e controllare il PARTUUID nell’ultima conferma.
5. Il piano vale dieci minuti. **Rigenera piano** riparte dal preflight e azzera le conferme. Durante l’intervento evitare operazioni concorrenti da altri strumenti di amministrazione. I controlli dell’app vincolano le sue sessioni, mentre gli strumenti esterni hanno accesso autonomo ai dispositivi.

Se la topologia è cambiata, il journal è incompleto, il firmware non identifica il membro alternativo o la ESP presenta firme di un filesystem, la modalità avanzata resta bloccata. Una FAT32 coerente dispone del piano di ripristino dedicato.

## Comandi e verifiche successive

Su un sistema con GRUB verificato il piano esegue, usando il percorso by-id della sola ESP:

```text
proxmox-boot-tool format <ESP>
proxmox-boot-tool init <ESP> grub
proxmox-boot-tool refresh
proxmox-boot-tool status
```

`format` prepara FAT32. Prima di `init` il nodo verifica FAT32, `fsck.fat -n`, UUID coerente e univoco, PARTUUID e identità del disco. `init` installa/configura GRUB e registra l'ESP; Proxmox esegue già un refresh interno, seguito dal refresh esplicito del piano. `refresh` sincronizza tutte le ESP registrate, quindi interessa anche il membro sano. Se il riferimento usa systemd-boot verificato, viene mantenuta quella modalità tramite `init <ESP>`, senza migrazione implicita a GRUB.

La diagnostica finale verifica UUID, registrazione, filesystem, bootloader, configurazioni/kernel e via di avvio UEFI (NVRAM o fallback riconosciuto). L'UUID appena creato e il PARTUUID devono ancora corrispondere. Un risultato incompleto non è dichiarato riuscito.

**UUID EFI vecchio assente:** il piano di inizializzazione non lo cancella. Dopo che entrambe le ESP risultano configurate, usare **Avvio EFI → Piano pulizia UUID assenti**, controllare gli UUID mostrati e confermare che non appartengano a dischi temporaneamente scollegati. Questo piano separato esegue `proxmox-boot-tool clean` e `status`. Poi aggiornare l'intervento EFI originario. Eventuali avvisi irrisolti mantengono `BOOT_REVIEW_REQUIRED`.

## Stati e interruzioni

| Stato | Significato |
| --- | --- |
| `WAITING_RESILVER` | ZFS sta ricostruendo; nessun comando EFI automatico. |
| `ESP_INITIALIZATION_REQUIRED` | Mirror ricostruito; occorre generare e approvare il nuovo piano ESP. |
| `ESP_INITIALIZATION_LINKED` | Intervento ZFS collegato al nuovo intervento EFI; consultare l'ID indicato. |
| `BOOT_REVIEW_REQUIRED` | Configurazione EFI o registro ancora da verificare. |
| `BOOT_TEST_PENDING` | Configurazione verificata; mancano le prove fisiche di avvio autonomo. |
| `COMPLETED` | Entrambi gli avvii autonomi registrati e verificati, mirror nuovamente ONLINE e configurazione ancora coerente. |

Se la connessione cade, riaprire **Interventi → Recupera interventi**. L’helper rilegge l’inventario. Se riconosce una FAT32 coerente con UUID sul disco e sulla ESP attesi dopo un tentativo di format, lo stato diventa `EFI_REPAIR_REQUIRED`. Usare **Prosegui con nuova ESP**, confrontare gli identificativi e approvare il piano di ripristino. Il journal conserva anche l’esito incerto iniziale. Se l’identità o il filesystem restano incerti, verificare la diagnostica prima di intervenire.

Se entrambe le ESP risultano configurate, aprire **Avvio EFI → Piano pulizia UUID assenti**. Confrontare gli UUID proposti con i dischi effettivamente dismessi. Confermare la lista soltanto dopo aver escluso che un disco registrato sia temporaneamente scollegato.

Se manca la voce del ricambio, **Registra voce UEFI mancante** confronta il file EFI sulla ESP con il loader usato da BootCurrent sul membro alternativo. La registrazione richiede percorso e contenuto coincidenti. Una voce esistente, anche inattiva, richiede diagnosi. Il comando `efibootmgr --create-only` crea la voce fuori da BootOrder. Verificare la nuova voce e usare **Prova avvio da questo disco** per il test BootNext. Se il firmware espone soltanto un fallback e BootCurrent non identifica il loader del disco alternativo, la creazione guidata resta bloccata.

Per i due avvii fisici seguire il [manuale](manuale.md): spegnere prima di scollegare nel laboratorio senza hot swap e risincronizzare il mirror fra una prova e l'altra. Nessun file EFI, UUID o comando `status` sostituisce queste prove.

## Collaudo

Test automatici con identità sintetiche: metadati GPT senza filesystem, firme e dati residui, conferma mancante, cambiamenti di identità, mirror non pronto, comandi ordinati, errori senza replay e stato finale in attesa di boot. Nessun comando di disco è stato eseguito su un server reale durante lo sviluppo: il collaudo hardware resta da effettuare nel laboratorio.

Riferimenti: [procedura ufficiale Proxmox per una nuova ESP](https://pve.proxmox.com/pve-docs/pve-admin-guide.html#sysboot_proxmox_boot_setup), [manuale blkid del progetto util-linux](https://github.com/util-linux/util-linux/blob/master/misc-utils/blkid.8.adoc).
