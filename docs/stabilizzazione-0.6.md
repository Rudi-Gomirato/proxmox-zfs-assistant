# Stabilizzazione e accettazione

**0.6.0rc1, 26 novembre 2026.** Candidata destinata al collaudo. Il primo banco previsto è il PC SATA senza hot swap con due mirror separati, ciascuno di due SSD. Il banco di produzione con chipset C252 richiede prove dedicate del controller, dei vani e del firmware. Le priorità sono sicurezza dei dati, affidabilità e ripresa (10/10), usabilità (9/10) ed estetica (5/10).

## Milestone e criteri di uscita

| Milestone | Consegna | Criterio per accettarla |
| --- | --- | --- |
| 0.6 · Motore, P0 | Inventario, selezione, protezioni, journal e ripresa | Dimostrare con dischi confondibili e WWN duplicati il blocco di ogni destinazione ambigua. Interrompere SSH e client in ogni fase. Riaprire lo stesso intervento senza rieseguire GPT, replace o format. Verificare controlli GUI e storico sul banco. |
| 0.7 · Sostituzione ZFS, P0 | Dischi dati e rpool, mirror, RAIDZ1/2/3, spare e resilver | Riprodurre membro assente e sostituzione interrotta per ciascuna topologia. Registrare GUID prima/dopo, confronto dei membri superstiti, esito della scansione e contatori. Provare attivazione dello spare e ritorno alla topologia prevista prima di abilitare quel percorso. |
| 0.8 · EFI e avvio, P0 | Recupero della sola ESP, GRUB e systemd-boot | Su mirror ONLINE recuperare ESP mancante e FAT32 esistente con approvazioni distinte. Dimostrare integrità del disco sano, registrazione UUID, loader e percorso UEFI. Ripetere l’avvio autonomo da ciascun membro. BIOS Legacy e Secure Boot richiedono casi dedicati. |
| 1.0 · Produzione | Compatibilità, istruzioni per tecnico junior e rapporto consegnabile | Tutte le 13 voci sotto documentate su hardware. Un tecnico completa il caso dalla GUI e dal report si ricostruiscono identità, comandi, prove ed eventuali interruzioni. |

## Revisione del codice e correzioni

| Area | Riscontro sul codice precedente | Cambiamento nella candidata |
| --- | --- | --- |
| Inventario e identità | Il seriale poteva corrispondere a una sottostringa di un alias. Nomi brevi restituiti da ZFS potevano rendere invisibile un disco presente. | Corrispondenza del seriale intero, alias univoco, rilevamento GUID mancanti o duplicati. Un membro presente riportato come numero si risolve confrontando le etichette locali del pool e del vdev con entrambi i GUID. I riferimenti storici restano consultabili. |
| Esecuzione e verifica | Il controllo finale cercava il ricambio in qualunque posizione del pool. Un nuovo stato ONLINE poteva bastare con uno scan precedente. | Si confrontano nodo, identità, posizione del membro, GUID superstiti, contatori e nuovo esito di resilver o scrub. Prima di replace si rileggono usi e appartenenza del ricambio. |
| Journal e concorrenza | Alcuni aggiornamenti di stato non prendevano il lock operativo. La scadenza dei job accodati dipendeva dall’ora dell’anteprima. Molte anteprime potevano nascondere un intervento pendente. | Lock comune, scritture atomiche con sincronizzazione su Linux, età a partire dall’approvazione. Il poll restituisce l’ultima registrazione durevole mentre il worker opera. Un avvio di worker interrotto richiede riconciliazione. |
| GUI e ripresa | Il refresh conservava alcune selezioni per solo alias. Un intervento EFI già collegato richiedeva di cercarlo nello storico. | La GUI verifica nodo e identità prima di ripristinare la selezione, recupera il journal alla riconnessione e apre il figlio EFI dal padre. In sola lettura mostra l’ultimo stato salvato. |
| Confini dei moduli | SSH e Qt sono separati dalla pianificazione, ma il servizio sul nodo coordina ancora inventario, worker e persistenza. | La riconciliazione usa lo stesso lock dell’esecuzione. La proiezione delle fasi è testabile senza Qt. Ulteriori estrazioni di moduli richiedono prima copertura dei percorsi ZFS e boot nelle milestone dedicate. |

### Caso del mirror ONLINE con ESP senza FAT32

La copia della GPT crea la partizione. Il resilver ricostruisce i dati del membro ZFS e non inizializza la partizione EFI. Il quadro segnalato è quindi compatibile anche con una ESP mai formattata sul ricambio. Per attribuire la perdita di un filesystem già esistente servono il journal precedente e successivo, gli UUID, le firme, i risultati di format/init e l’identità fisica. Queste evidenze del guasto originale non sono disponibili.

Dopo la riconnessione, aprire **Interventi** e selezionare la sostituzione. **Prosegui con nuova ESP** richiede un nuovo piano con destinazione e provenienza verificate. **Apri intervento EFI collegato** riapre quello già approvato. Una FAT32 esistente segue il ripristino. Una ESP priva di FAT32 segue il percorso di inizializzazione con le conferme previste. Il dettaglio distingue ZFS, configurazione EFI e prova fisica ancora pendente. [Procedura EFI](nuova-esp.md)

## Collaudo ripetibile

1. Usare un nodo sacrificabile, copie di sicurezza verificate e un secondo sistema per raccogliere i log. Registrare versione PVE, kernel, ZFS, firmware, controller, layout GPT e identità dei membri.
2. Confrontare inventario GUI ed etichette fisiche. Provare WWN duplicato, seriale ambiguo, ricambio troppo piccolo, disco montato, disco sano e operazione concorrente. Per ogni blocco conservare piano e motivo.
3. Scollegare il client subito dopo l’approvazione e durante il resilver. Riaprire l’app e collegarsi allo stesso nodo. Verificare ID intervento invariato, nessuna duplicazione di comandi ed esito coerente con il nodo. Simulare separatamente l’arresto del worker sul banco di prova.
4. Sul mirror di boot, conservare lo stato dei due membri e della ESP sana prima/dopo. Dopo il resilver completare solo EFI. Per ciascun avvio autonomo registrare quale disco era fisicamente presente, ID di avvio e BootCurrent. Risincronizzare il mirror prima dell’altra prova.
5. Esportare il rapporto e allegare log, versione esatta, esito atteso e osservato, operatore, data e limitazioni. Ripetere prima in un laboratorio virtualizzato, poi sulle piattaforme fisiche effettivamente supportate.

**Evidenze disponibili per questa candidata:** test automatici con inventari sintetici e build Windows. Il resilver reale riferito dall’operatore per una versione precedente è un dato storico. Il suo esito non convalida questa build. Restano da eseguire prove SSH con interruzioni reali, hardware SATA/SAS/NVMe, hot swap e LED, firmware, avvio indipendente e Proxmox VE 9.3. Le prove sintetiche sulle topologie esistenti restano nel repository. Il vecchio profilo operativo produzione è bloccato per sostituzione e offline. I profili laboratorio consentono il collaudo controllato, con hot swap soltanto su hardware predisposto. Le nuove connessioni partono in sola lettura.

## Accettazione produzione · 0/13

Ogni voce richiede l’identificativo del rapporto di prova della build candidata prima della firma di accettazione.

- [ ] Identificazione univoca dei dischi
- [ ] Protezione dalla cancellazione accidentale del disco sano
- [ ] Sostituzione dei dischi dati
- [ ] Sostituzione dei dischi rpool
- [ ] Riparazione EFI indipendente
- [ ] Gestione corretta di GRUB e systemd-boot
- [ ] Ripresa dopo disconnessione SSH
- [ ] Ripresa dopo chiusura dell’applicazione
- [ ] Assenza di falsi esiti positivi nei casi di collaudo
- [ ] Avvio verificato dal disco sostitutivo
- [ ] Test superati su hardware fisico
- [ ] Compatibilità verificata con Proxmox VE 9.3
- [ ] Report tecnico completo
