# Modifiche

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
