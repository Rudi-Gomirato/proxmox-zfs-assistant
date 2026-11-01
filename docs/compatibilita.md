# Compatibilità e stato del collaudo

Versione iniziale 0.1.0, 1 novembre 2026. Il codice è implementato e sottoposto a test automatici e verifica della GUI su Windows. Non è stato collegato a un server Proxmox reale durante lo sviluppo di questa versione.

| Configurazione | Diagnostica implementata | Esecuzione implementata | Verifica effettuata |
| --- | --- | --- | --- |
| Windows desktop | Sì | Client SSH e GUI | Test automatici e avvio demo |
| Proxmox VE 8 e 9 | Raccolta e verifica famiglia | Piani sul nodo | Fixture; collaudo reale necessario |
| VM mirror di due dischi interi | Sì | Replace senza forzatura | Test su dati sintetici |
| VM GPT ZFS con eventuale partizione riservata | Sì | Replica GPT e replace della partizione | Test su dati sintetici |
| VM RAIDZ1 o RAIDZ2 di tre membri | Sì | Replace del membro nel gruppo esistente | Test su dati sintetici; serve banco aggiuntivo |
| Boot ZFS mirror di due SSD con ESP | Sì | GPT, replace, format e init ESP | Test su dati sintetici per GRUB e systemd-boot |
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
