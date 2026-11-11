# Correzioni e refactoring 0.4.1

Questo aggiornamento corregge i problemi diagnosticati durante il recupero del mirror e riorganizza le schermate esistenti. Non introduce operazioni di cancellazione forzata, retry automatici o nuovi percorsi di riparazione.

## Nuova ESP: falso blocco dei metadati

L'export `blkid -p -o export <ESP>` può iniziare con `DEVNAME=<ESP>`, seguito dai metadati GPT `PART_ENTRY_*`. Il vecchio controllo trattava `DEVNAME` come una firma sconosciuta e bloccava la procedura prima della lettura dei dati. Il parser ora ammette quel campo solo se identifica esattamente il percorso richiesto. Riferimento: [implementazione util-linux di blkid](https://github.com/util-linux/util-linux/blob/master/misc-utils/blkid.c).

Restano obbligatori: risultati coerenti di blkid/wipefs, apertura del dispositivo in sola lettura esclusiva, dimensione verificata e lettura di ogni byte della ESP (massimo 4 GiB). FSTYPE assente non significa disco vuoto. Firme, byte non nulli, errori, letture incomplete o scadenza del tempo bloccano la formattazione. Il piano riporta i risultati degli strumenti e distingue «lettura non avviata» dai byte realmente letti.

Il confronto WWN normalizza prefisso e separatori. Un WWN condiviso non identifica da solo un disco: servono un seriale univoco e un alias persistente indipendente contenente il seriale, verificato sul medesimo dispositivo durante l'esecuzione. I controlli PARTUUID, tipo ESP, separazione da ZFS, provenienza nel registro e conferma esplicita del format restano in vigore.

## Errore del comando e stato corrente

Un comando registrato FAILED o UNKNOWN conserva l'esito originale e tutti i suoi eventi. L'aggiornamento dell'intervento osserva il pool e cerca il ricambio tramite identità, membro ZFS e, se registrata, ESP. Mostra separatamente RESILVERING, ONLINE oppure UNCONFIRMED.

Un pool ONLINE non prova chi abbia effettuato la sostituzione e non certifica il boot. Questa osservazione non rilancia GPT/replace/format e non rende autorizzabile una ESP se manca un intervento riuscito e verificabile nel registro. Un caso storico con `replace` fallito richiede quindi ancora diagnosi: non viene trasformato artificialmente in un successo. Dopo l'inizializzazione EFI, BOOT_TEST_PENDING rimane fino alle prove fisiche previste.

## Organizzazione del codice e interfaccia

| Modulo | Responsabilità |
| --- | --- |
| `identity.py` | Confronti dell'identità fisica, senza comandi |
| `esp.py` | Prove conservative della ESP vuota e diagnostica delle letture |
| `workflow.py` | Stati, prossimo passo e osservazione ZFS in sola lettura |
| `presentation.py` | Rendering HTML con escaping dei dati |
| `ui_theme.py`, `ui_widgets.py` | Tema, navigazione e dettagli espandibili |
| `gui.py` | Collegamento dei controlli alle azioni già disponibili |

Le sei sezioni restano disponibili nella barra laterale. Interventi mostra prima stato e prossimo passo; i comandi eseguiti sono espandibili. Sostituzione mantiene l'anteprima dei comandi prima dell'approvazione. Avvio EFI distingue esplicitamente ripristino di una ESP esistente e inizializzazione del ricambio. I pulsanti che richiedono una selezione identificata sono disabilitati quando manca.

## Aggiornamento e collaudo

1. Attendere che non vi siano comandi in esecuzione. Aggiornare l'app e installare esplicitamente l'helper 0.4.1 da Connessione.
2. Ripetere l'analisi e recuperare gli interventi dal nodo.
3. Sul laboratorio, verificare metadati GPT senza filesystem, un caso con dati residui e la corretta associazione per seriale/WWN/PARTUUID. Il caso con dati residui deve restare bloccato.
4. Verificare che un errore storico non venga cancellato quando il pool torna ONLINE; nessuna ripetizione automatica.
5. Per una sostituzione verificabile, controllare il piano EFI, la conferma del format e le prove fisiche finali secondo il [manuale ESP](nuova-esp.md).

La suite automatica usa esclusivamente dati sintetici: copre questi casi, le conferme e la stabilità del refresh GUI. Le schermate e il pacchetto Windows sono verificati localmente. Questo refactoring non è un collaudo su hardware Proxmox reale.
