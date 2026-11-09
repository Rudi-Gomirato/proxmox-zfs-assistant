# Compatibilità e stato del collaudo

> Aggiornamento 0.4.0: la nuova ESP richiede un piano distinto con conferma di formattazione; non riprende automaticamente dopo il resilver. Il ripristino delle ESP esistenti non formatta. Vedi la [procedura corrente](nuova-esp.md).

Aggiornamento 0.3.0: [comportamento attuale, estensioni e collaudo](collaudo-0.3.md). Le limitazioni precedenti descritte sotto sono aggiornate da questa nota.
Versione 0.2.0, 2 novembre 2026. Il codice è implementato e sottoposto a test automatici e verifica della GUI su Windows. Non è stato collegato a un server Proxmox reale durante lo sviluppo di questa versione. Le regressioni del caso EFI usano esclusivamente dati sintetici; non attribuiscono una causa al guasto segnalato.

| Configurazione | Diagnostica implementata | Esecuzione implementata | Verifica effettuata |
| --- | --- | --- | --- |
| Windows desktop | Sì | Client SSH e GUI | Test automatici e avvio demo |
| Proxmox VE 8 e 9 | Raccolta e verifica famiglia | Piani sul nodo | Fixture; collaudo reale necessario |
| VM mirror di due dischi interi | Sì | Replace senza forzatura | Test su dati sintetici |
| VM GPT ZFS con eventuale partizione riservata | Sì | Replica GPT e replace della partizione | Test su dati sintetici |
| VM RAIDZ1 o RAIDZ2 di tre membri | Sì | Replace del membro nel gruppo esistente | Test su dati sintetici; serve banco aggiuntivo |
| Boot ZFS mirror di due SSD con ESP UEFI x64 | Diagnosi di entrambi i membri | GPT, replace, attesa resilver, ESP, init e refresh | Test sintetici per GRUB e systemd-boot; collaudo fisico necessario |
| Mirror ONLINE con una ESP assente o incompleta | Controlli separati ZFS/EFI | Piano sulla sola ESP selezionata, con riferimento sano | Regressioni sintetiche; nessun replace su pool sano |
| Avvio autonomo di ciascun membro | Configurazione e BootCurrent | Registrazione operatore dopo due avvii fisici distinti | Test del flusso con fixture, prove hardware necessarie |
| Secure Boot | Stato rilevato quando disponibile | Nessuna certificazione firme; systemd-boot bloccato se attivo | Catena firmata e firmware da verificare manualmente |
| GRUB legacy o layout boot personalizzato | Rilevamento parziale | Bloccata | Procedura manuale dedicata |
| RAIDZ3, mirror più ampi, più vdev | Rilevamento topologico | Bloccata | Non nel perimetro esecutivo iniziale |
| Log, cache, special, spare attivi, dRAID | Rilevamento conservativo | Bloccata | Non nel perimetro esecutivo iniziale |
| PC laboratorio SATA senza hot swap | Percorso previsto | Spegnimento, poi nuova analisi e piano | Verifica fisica da eseguire |
| Xeon E-2300 / Intel C252 con hot swap | Profilo previsto | Offline e replace condizionati ai controlli | Modello e backplane da collaudare |
| LED sysfs enclosure locate | Discovery e timer | Supporto implementato | Hardware non collaudato |
| LED ledctl, sg_ses diretto, NVMe vendor | Nessun backend dedicato | Non implementata | Estensione futura |

Il parser della versione iniziale usa l'output testuale di `zpool status` con locale C e letture coerenti di percorsi e GUID. La modalità JSON di OpenZFS non è ancora usata; gli output non riconosciuti bloccano le modifiche e producono una richiesta di chiarimento. I nomi disco non sono una prova del livello RAID.

La capacità del ricambio viene confrontata conservativamente con il più grande disco del vdev osservato, non solo con una dimensione nominale commerciale. Si richiedono settori logici coerenti, seriale univoco e percorso by-id verificato. La prima versione rifiuta dischi con firme o partizioni, non implementa pulizia automatica e non modifica la topologia per aggirare un errore.

La topologia e le firme sono ricontrollate sul nodo prima dell'esecuzione. Questo non equivale a una certificazione dell'hardware né a una garanzia contro altri amministratori che modifichino il server durante una manutenzione: concordare sempre la finestra di intervento.

Il collaudo fisico deve registrare release Proxmox, OpenZFS, kernel, metodo di boot, Secure Boot, modello server/scheda madre, cablaggio o backplane, strumenti installati e sequenza degli esiti. Per RAIDZ il PC con due dischi VM non basta: iniziare con tre dischi virtuali sacrificabili, poi hardware rappresentativo con dati di prova. Nessun guasto indotto su VM operative.

Per il boot provare sia sostituzione sia ripristino della sola ESP con GRUB e systemd-boot, controllare che la destinazione sia sempre distinta dal membro ZFS e che i passi EFI attendano il resilver. Verificare interruzioni SSH, UUID obsoleti, file mancanti e differenze firmware. Le due prove di boot richiedono risincronizzazione con entrambi i membri fra una prova e l'altra, come indicato nel manuale. Non è ancora certificata la portabilità della diagnostica con ESP già montate o con percorsi firmware proprietari.
