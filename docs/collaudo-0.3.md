# Versione 0.3.0: aggiornamento e collaudo

Aggiornare app e helper dalla scheda Connessione a interventi conclusi. La compatibilità di versione è verificata prima dei piani. Il codice è verificato con fixture sintetiche e GUI Windows; le correzioni non attestano un collaudo sul server reale.

## Percorso del laboratorio

1. Avviare il pacchetto Windows completo oppure eseguire installa.cmd e avvia.cmd. L'installer accetta Python >= 3.10 tramite py oppure python nel PATH e mantiene visibili gli errori.
2. Salvare un profilo con etichetta cliente/nodo/ambiente. Password e passphrase non sono salvate. Dopo Carica profilo premere Collega e analizza: caricare il profilo non collega automaticamente un altro server.
3. Selezionare il pool root per GUID, il membro UNAVAIL e il ricambio per by-id/seriale. Provare refresh manuale e automatico, anche dalla scheda Sostituzione. Il target deve restare lo stesso. Se scompare, scegliere esplicitamente il nuovo contesto; il software non ne sceglie un altro.
4. Aprire Avvio EFI. Per GRUB, una configurazione letterale che punta a /boot nel dataset root ZFS viene controllata con accessibilità dei file e findmnt. Non sono obbligatori kernel sulla ESP. Sono supportate anche entry ESP con percorsi verificabili. Per systemd-boot sono controllati tutti i percorsi linux/initrd indicati nelle entry. Variabili, script o dataset alternativi non riconosciuti restano UNKNOWN e richiedono diagnosi dedicata.
5. Se la ESP superstite richiede reinstallazione del loader, il piano di ripristino è disponibile anche con mirror degradato, purché FAT32, registro, identità e configurazione siano verificati e gli altri membri assenti. Non formatta l'unica ESP; filesystem corrotto, modalità ignota o dati incerti bloccano il piano. Dopo il ripristino ripetere l'analisi e creare un piano di sostituzione distinto.
6. Anteprima completa dei comandi, conferma backup, identificazione fisica e seriale; poi sostituzione ZFS e monitoraggio. La fase EFI riprende dopo resilver dal cursore persistente. UNKNOWN non viene ritentato.
7. Gli UUID assenti sono avvisi di registro, non prove di guasto della ESP superstite. Dopo mirror ONLINE e configurazione EFI verificata su entrambi, usare Piano pulizia UUID assenti. Confermare che quei dispositivi non siano temporaneamente scollegati. Il piano approvato esegue proxmox-boot-tool clean e status, con nuovo controllo di fingerprint prima della pulizia.
8. Eseguire le prove fisiche di avvio del manuale: spegnere prima di scollegare nel laboratorio, risincronizzare entrambi i dischi tra le due prove. La presenza dei file non certifica firmware o Secure Boot.

## Sola lettura e privilegi

La casella Sola lettura configura il collegamento con helper `--read-only`. Consente raccolta, elenco dei registri e dry-run; rifiuta invio, LED, spegnimento e anche status operativo, che potrebbe riprendere passi EFI. Il dry-run esegue controlli e costruisce comandi senza salvare un piano eseguibile. Il registro storico in questa modalità non aggiorna automaticamente l'esito dei job.

La casella è una protezione applicativa. Per imporre il vincolo a un account, l'amministratore deve autorizzare in sudoers soltanto il comando esatto con `--read-only`, senza l'altra regola operativa. L'helper diagnostico richiede ancora privilegi root per leggere dispositivi e ESP; non è un'implementazione di raccolta senza privilegi. L'installazione dell'helper resta un'azione amministrativa esplicita.

## Ambito delle estensioni

Sostituzione dati: mirror >= 2 membri, RAIDZ1/2/3 con almeno parità+1 membri, anche più vdev dati. Tutti gli altri membri e vdev devono essere sani e identificabili; una sostituzione già attiva, spare, cache/log/special, dRAID, GUID duplicati e layout sconosciuti bloccano le scritture. Il boot resta limitato a un mirror di due membri e layout GPT riconosciuti. Il numero di dischi non viene usato per dedurre il RAID.

Il riconoscimento di possibili volumi RAID hardware dal modello è un avviso euristico: l'assenza dell'avviso non prova che l'HBA esponga dischi fisici. Slot e LED restano dipendenti dall'enclosure sysfs. SMART/NVMe e metriche mancanti sono N/D; ETA e velocità sono quelle riportate da ZFS, senza previsioni inventate.

Il rapporto esporta snapshot e cronologia in JSON, HTML o PDF anonimizzati. Comandi e output completi rimangono nei registri privati sul nodo; l'esportazione maschera anche testi liberi per evitare divulgazioni accidentali. I registri conservano operatore, fase, target, tempi, return code e prove prima/dopo. La retention locale è configurabile da 10 a 10000 snapshot; SQLite riutilizza lo spazio liberato senza VACUUM automatico durante la GUI.

## Verifiche automatiche

Regressioni per 100 refresh, pool scomparso, risposta obsoleta, piano invariato/cambiato, GRUB con kernel su ZFS, file mancanti, percorsi ignoti, più initrd systemd, recupero superstite, pulizia UUID, topologie estese e blocchi di sola lettura. CI Windows/Linux Python 3.11/3.12, installazione senza launcher e build con prova di avvio dell'EXE.
