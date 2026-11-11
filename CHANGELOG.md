# Modifiche

## 0.4.1 — 11 novembre 2026

- Risolto il falso blocco della nuova ESP: `DEVNAME` nell'export di `blkid` è metadato ammesso solo se coincide con la partizione richiesta. Firme, output ambigui e dati non nulli continuano a bloccare la formattazione.
- Conservati risultati degli strumenti, stato della scansione e byte effettivamente letti anche in caso di errore, senza mostrare una verifica di zero byte se la lettura non è partita.
- Centralizzati confronto WWN e identità composta; WWN condivisi richiedono anche seriale univoco e alias indipendente verificato sul dispositivo al momento dell'esecuzione.
- Affiancato agli interventi `replace` FAILED/UNKNOWN lo stato ZFS corrente, senza riscrivere l'esito, ripetere il comando o usare il solo ONLINE come prova di provenienza per la formattazione.
- Separati presentazione HTML, tema, widget e vocabolario degli stati dalla finestra principale. Navigazione laterale, prossimo passo e log espandibili; conferme e comandi del piano sempre consultabili.
- Aggiunte regressioni per parser, identità, letture incomplete, riconciliazione in sola lettura e navigazione GUI. Nessuna nuova API distruttiva.

## 0.4.0 — 9 novembre 2026

- Separati ripristino FAT32 esistente e inizializzazione della nuova ESP dopo sostituzione. Nessun format nel ripristino e nessuna ripresa EFI automatica dopo il resilver.
- Nuovo piano da Avvio EFI o dall'intervento ZFS: prova di provenienza nel registro root del nodo, WWN/seriale/PARTUUID, resilver concluso, distinzione ESP/ZFS, verifica firme e lettura integrale di tutti i byte.
- Riconosciuto il risultato blkid con soli metadati GPT; dati non nulli o esiti incerti mantengono il blocco. Conferma esplicita di format vincolata al PARTUUID, oltre al seriale.
- FAT32 e UUID verificati prima di init, GRUB mantenuto quando rilevato sul riferimento, refresh esplicito e diagnostica finale. Pulizia vecchi UUID separata e BOOT_TEST_PENDING fino alle prove fisiche.
- Se il comando diagnostico status termina con errore dopo le scritture, conserva il codice nel registro e mantiene BOOT_REVIEW_REQUIRED: dopo la pulizia separata è possibile rivalutare lo stesso intervento senza ripetere format/init. I comandi modificativi falliti non vengono ritentati.
- Registro padre/figlio per recuperare la fase EFI senza ripetere GPT/replace, controlli ripetuti prima delle scritture e nessun replay dopo errori.
- Nuove regressioni sintetiche e guida [nuova ESP](docs/nuova-esp.md); collaudo su hardware reale ancora necessario.

## 0.3.0 — 9 novembre 2026

- Chiusura esplicita delle finestre Qt e dei timer; teardown dei test prima della finalizzazione Python. Vincolo PySide6 6.10.3 su Linux/Python < 3.12 per l'arresto nativo `none_dealloc` osservato con 6.12.0 dopo i test; Windows e Python >= 3.12 mantengono 6.12.0.

- Corretto il cambio automatico del pool: selezioni stabili, nessun fallback, nessun cambio da richieste di chiarimento RAID; controlli del target congelati durante le richieste SSH.
- Ignorati snapshot precedenti; piani conservati se il fingerprint di sicurezza è invariato, con ordine degli elementi normalizzato. Cambiamenti reali richiedono nuova approvazione.
- Distinte entry GRUB/ZFS e systemd-boot, con percorsi verificati e risultati ERROR/UNKNOWN. Nessuna esecuzione degli script di configurazione durante la diagnosi.
- Ripristino della ESP superstite su mirror degradato limitato a FAT32 coerente, registrazione e configurazione riconosciute, senza formattazione. Pulizia UUID assenti con piano separato dopo verifica dei due membri.
- Estese le sostituzioni dati a mirror di più membri, RAIDZ1/2/3 e più vdev sani. Spare e topologie non collaudate restano bloccati.
- Profili di connessione, modalità sola lettura applicata anche dall'helper, dry-run senza salvataggio di un piano eseguibile, retention configurabile e cronologia privata.
- Rapporto anonimizzato JSON/HTML/PDF con interventi; evidenze prima/dopo e operatore nel registro del nodo, fasi e target per comando.
- Dettagli SMART/NVMe, capacità/frammentazione, avviso euristico di volume RAID, velocità ed ETA quando riportate da ZFS.
- Installer con fallback dal launcher a python, controllo versione e pausa sugli errori; test CI senza launcher Python.

## 0.2.0 — 2 novembre 2026

- Separati gli esiti ZFS, resilver, configurazione EFI e avvio autonomo. Un mirror ONLINE non conclude più un intervento sul boot.
- Aggiunta la scheda Avvio EFI: controlli indipendenti su entrambi i dischi per ESP GPT, FAT32, coerenza del filesystem, UUID/PARTUUID, registro proxmox-boot-tool, file EFI, kernel/initrd e percorso UEFI. Ispezione in sola lettura in una namespace di mount privata.
- Nella sostituzione boot la fase EFI attende la ricostruzione ZFS. Il cursore persistente riprende solo i passi ancora da eseguire, senza ripetere GPT o replace. Aggiunto refresh esplicito delle ESP registrate.
- Aggiunto un piano approvabile per ripristinare la sola ESP selezionata con mirror sano: nessuna ricreazione GPT o sostituzione ZFS. Una FAT32 valida viene conservata; la formattazione richiede prove dirette dell'assenza di firme.
- Aggiunti BOOT_REVIEW_REQUIRED e BOOT_TEST_REQUIRED. Anche i vecchi interventi boot COMPLETED vengono rivalutati. Le due prove fisiche registrate dall'operatore richiedono avvii distinti, un solo membro presente e BootCurrent coerente; cambiare le evidenze EFI invalida le prove precedenti.
- Richiesto l'aggiornamento dell'helper alla stessa versione dell'app prima di usare piani e interventi.
- Gli errori di avvio con pythonw o EXE producono un log e un messaggio visibile. Chiarito che la chiave privata SSH è facoltativa con password o agente.
- Aggiunte regressioni sintetiche per il caso mirror ONLINE con ESP non avviabile. CI su Windows e Linux con Python 3.11/3.12 e avvio di prova dell'EXE compilato.

La diagnostica non certifica le firme Secure Boot né sostituisce il collaudo fisico del firmware. Nessun log identificativo del caso segnalato è incluso nel repository.

## 0.1.0 — 1 novembre 2026

Prima implementazione della GUI Windows, dell'helper SSH, della diagnosi ZFS e delle sostituzioni guidate per mirror e RAIDZ nel perimetro documentato.
