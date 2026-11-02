# Segnalazioni di sicurezza

Non pubblicare credenziali, chiavi private, inventari o dettagli sfruttabili in un'issue pubblica. Quando il repository è disponibile, usa una segnalazione privata nella scheda Security se abilitata; altrimenti contatta privatamente il manutentore prima di inviare dettagli sensibili.

L'helper è privilegiato e deve appartenere a root, con directory non modificabili dagli utenti ordinari. Le regole sudo devono indicare il comando esatto documentato nel manuale. L'app non accetta host key cambiate e non salva password nel database.

La versione 0.2.0 è da collaudare in laboratorio. Le verifiche automatiche non rappresentano una certificazione per dati di produzione. Un esito ignoto non deve essere risolto ripetendo comandi distruttivi. Lo stato ONLINE di ZFS non dimostra che ogni disco possa avviare il server; la diagnostica EFI e le prove fisiche di avvio sono verifiche distinte.
