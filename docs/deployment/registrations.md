# Registrazioni e affidabilità email

Per questa release seguire prima il [runbook aggiornamento RegForm ed Editor](regform-dashboard-update.md).
L’Editor canonico è ora integrato nella dashboard: il sibling non è una dipendenza runtime.

## Confini e autorità

La dashboard espone **Conference sites → Registrations** agli amministratori
MIFP già autorizzati. La fonte delle iscrizioni è il CSV PHP
`/srv/mifp-events-private/registrations/<EVENTO>/registrations.csv`; le ricevute
sono in `uploads/<EVENTO>/`. La chiave `<EVENTO>` è il nome della directory del
microsito, come nel regform portabile. Due siti con la stessa chiave finale
sono bloccati dalla dashboard: risolvere l'ambiguità prima di amministrarli.

Il CSV non viene importato nel DB applicativo né modificato per aggiungere stati.
Stati, note, archivio e storico essenziale stanno nel piccolo sidecar SQLite
`/opt/mifp/data/conferences/.registrations-admin/registrations-admin.sqlite3`.
Questa directory è privata e rientra nel backup esistente di `conferences/`.
L'archiviazione è un metadato reversibile: conserva iscrizione e allegati.
Gli export CSV/XLSX includono i risultati filtrati e i metadati amministrativi; Word/PDF includono la lista e copie delle ricevute. Ogni export richiede password e POST con CSRF;
le celle vengono protette dalla formula injection. I download degli allegati
sono autenticati, non hanno filename scelti dal client e non sono cacheabili.
CSV malformati, receipt duplicati, symlink e percorsi ambigui bloccano la lettura.

Per impostazione predefinita il container riceve **due bind mount in sola lettura**, registrazioni e
upload, attraverso `compose.registrations.yaml`, selezionato esplicitamente da
`mifpctl` quando `REGISTRATIONS_DASHBOARD_ENABLED=true` e il backend è `local-vps`.
Il gruppo supplementare è quello dell'utente PHP; consente di leggere file
`0640` in directory `0750`, ma il mount impedisce scritture. Non vengono montati
`/etc/msmtprc`, credenziali, sessioni, socket host o l'intero albero privato.
La [cancellazione protetta dalla dashboard](regform-dashboard-update.md#cancellazione-dalla-dashboard-ed-export-protetti) richiede un opt-in separato alla scrittura dei soli due mount e un runtime PHP aggiornato; senza opt-in nulla cambia. Gli altri backend mantengono i mount e il comportamento ordinari. Se lo storage
non è collegato, la dashboard indica indisponibilità; non presenta dati vuoti
come prova che non esistano iscrizioni. Anche le installazioni locali possono
usare la cartella privata automatica descritta sotto, o configurare
`REGISTRATIONS_PRIVATE_ROOT` per un archivio esistente.

## Difetti email confermati e significato degli stati

Due difetti sono riproducibili senza accedere alla VPS:

- `MAIL_PROVIDER=enabled` con `SMTP_HOST` veniva interpretato come SMTP dal relay,
  ma non da `mifpctl status`. La lettura della configurazione host normalizza ora
  il valore legacy per tutti i consumatori; `disabled` e `console` restano tali.
- `msmtp --file=/etc/msmtprc` carica il file come **configurazione utente**:
  msmtp rifiuta credenziali in un file leggibile dal gruppo, anche con modo `0640`.
  PHP-FPM usa un file dedicato `/etc/msmtprc-mifp-events`, owner
  `mifp-events:mifp-events`, modo `0600`, generato atomicamente dal tooling host.
  Monitor e retry root usano `/etc/msmtprc` root:root `0600` come configurazione
  di sistema, con `--file=/dev/null --account=default`. Non copiare credenziali
  nei WEBSITE. Le rotazioni passano dal wizard mail e aggiornano entrambi i file.

Questo spiega un errore reale del comando generato, ma non dimostra che sia
l'unica causa della mancata consegna sulla VPS: credenziali, mittente Aruba,
TLS, destinatari e filtri richiedono le verifiche host sotto indicate.
La semantica è coerente con il [manuale msmtp](https://marlam.de/msmtp/msmtp.html#Configuration-files)
e con il [contratto di PHP mail()](https://www.php.net/manual/en/function.mail.php):
accettazione del trasporto e consegna finale sono eventi distinti.

Le nuove versioni del regform conservano separatamente
`<RECEIPT>.notifications.json`, con canali `organizers` e `participant`:

| Stato | Significato | Retry |
| --- | --- | --- |
| pending | Nessun tentativo iniziato | Richiesta esplicita |
| sending | Tentativo iniziato, esito non ancora persistito | Bloccato |
| accepted | Trasporto ha accettato il messaggio | Bloccato; non prova la consegna |
| failed | Operatore ha attestato una mancata accettazione | Richiesta esplicita |
| unknown | Tentativo fallito o interrotto con esito ambiguo | Bloccato fino a verifica host |
| not_requested | Conferma partecipante disattivata nelle impostazioni | Bloccato |
| unavailable | Iscrizione precedente senza tracciamento | Nessuna presunzione sull'invio |

L'iscrizione viene sincronizzata su disco prima delle notifiche. Errori di
configurazione destinatari o SMTP non eliminano CSV e ricevuta già salvati.
Il lock `.operations.lock` sincronizza regform e operazioni host; il CSV continua
ad usare `flock` sul proprio inode, e ogni notifica ha il proprio lock. Prima di
chiamare il trasporto viene persistito `sending`: una caduta del processo non
abilita un retry cieco. SMTP non garantisce consegna exactly-once quando si perde
la risposta del server: si conserva l'incertezza invece di promettere l'assenza
assoluta di duplicati. Non sono previsti retry automatici.

## Aggiornamento esplicito della VPS

Questi sono comandi da eseguire **dopo revisione e deploy autorizzato**, non una
procedura già eseguita. Non cambiare DNS, credenziali o parametri SMTP Aruba
per applicare questa correzione. I relay derivati vengono generati automaticamente
dalla configurazione canonica; non richiedono copie manuali dei segreti.

1. Verificare e conservare un backup corrente con gli strumenti esistenti:
   `sudo mifpctl backup`. Aggiornare l'intero bundle host usando il
   [trasferimento con staging root-owned](vps-installation.md#host-tools-refresh).
   Il refresh installa helper e override Compose, ma non applica Caddy,
   non modifica il pool PHP live e non riavvia l'app.
2. Revocare il modulo prima della sostituzione:
   `sudo mifpctl events-php-disable PLMCN-2027/regform`.
   Non procedere se revoca/reload fallisce. Anche la pulizia **host** dei dati esige
   che non esistano autorizzazioni attive per quella chiave di storage.
3. Dopo il refresh, applicare la configurazione host revisionata con
   `sudo mifpctl host-apply all`, quindi `sudo mifpctl mail-diagnose`.
   Il comando crea/aggiorna pool e relay dedicato, imposta automaticamente
   owner/mode e conserva i limiti `10M`/`12M`. Non serve modificare il pool
   manualmente né rieseguire il bootstrap sulla VPS esistente.
4. Dall'Editor aggiornato, aprire il **WEBSITE autorevole fornito**, non una
   copia ZIP precedente. Il round-trip ZIP preserva deliberatamente i file:
   per un progetto importato già esistente applicare anche i nuovi
   `regform/index.php` e `regform/src/bootstrap.php` al progetto prima dell'export.
   La cartella PLMCN-2027 accessibile è stata aggiornata con queste sole patch;
   i suoi metadati coincidono con gli ZIP autorevoli. Non modificare prezzi,
   privacy, apertura o destinatari senza conferma dell'organizzatore.
   Assegnare una nuova versione tecnica con il flusso release dell’Editor ed
   esportare un **nuovo** WEBSITE distinto dall'originale. INFO non cambia schema.
5. Attendere CI verde e aggiornare la dashboard con `sudo mifpctl check` e
   `sudo mifpctl update`. Importare il nuovo WEBSITE con sostituzione e rollback
   dalla dashboard, mentre PHP resta revocato. La protezione della pubblicazione
   contro autorizzazioni precedenti resta attiva.
6. Per le iscrizioni esistenti, predisporre i soli permessi necessari:

   ```bash
   sudo mifpctl registrations-access PLMCN-2027
   sudo mifpctl config-set REGISTRATIONS_DASHBOARD_ENABLED true
   sudo mifpctl restart
   ```

   `registrations-access` non modifica i contenuti: valida i file, prepara il
   lock operativo e imposta `0640`/`0750` solo su CSV, ricevute e stati/lock notifica
   riconosciuti di questa conferenza. Non tocca sessioni, altri eventi o file
   arbitrari. Il nuovo PHP mantiene quei modi soltanto quando l'host espone
   `MIFP_REGISTRATION_READ_GROUP=1`; su hosting tradizionale resta `0600`/`0700`.
   Per una conferenza senza CSV, il nuovo regform crea lo storage al primo
   accesso/invio; non creare iscrizioni fittizie sulla VPS per inizializzarlo.
7. Eseguire la diagnostica, inizialmente senza rete:

   ```bash
   sudo mifpctl mail-diagnose
   # Facoltativo: connessione TLS e autenticazione, senza MAIL FROM/RCPT/DATA.
   sudo mifpctl mail-diagnose --network
   sudo mifpctl config-check
   sudo mifpctl security-check
   ```

   Il comando verifica normalizzazione, coerenza della configurazione relay,
   ownership/permessi, leggibilità e parsing msmtp come utente PHP, `sendmail_path`.
   La modalità rete, solo dopo i controlli locali, verifica connessione/TLS e
   autenticazione SMTP senza inviare messaggi. Non stampa password, indirizzi,
   corpi email o stderr SMTP. Un errore resta `FAILED`: investigarlo localmente
   senza pubblicare log grezzi o usare `msmtp --debug` nei report.
8. Rivedere il codice pubblicato e autorizzare esplicitamente:
   `sudo mifpctl events-php-enable PLMCN-2027/regform`.
   Verificare `/PLMCN-2027/regform/` e gli accessi negati a `settings.yaml`, `src/`,
   `registrations/` e dotfile. I dati privati non richiedono alcun routing Caddy.
   Un'eventuale applicazione di nuovi template/routing Caddy resta un'operazione
   distinta: `sudo mifpctl host-apply web`, dopo revisione.

## Registrazioni in locale: cartella privata automatica

Con `FLASK_ENV=development`, senza override, la pagina usa automaticamente
`CONFERENCES_DIR/.registrations-private/`. Con `./mifp local` corrisponde a
`MIFPAPP/DATABASE/conferences/.registrations-private/`; con Docker locale è
`/app/data/conferences/.registrations-private/`, sul volume dati già esistente.
Vengono create soltanto directory private `0700`, mai record sintetici o CSV.
Nessuna API o sincronizzazione è necessaria.

I file hanno questa struttura:

```text
.registrations-private/
  registrations/EVENTO/registrations.csv
  uploads/EVENTO/proof-REG-....png
```

La pagina indica esplicitamente che sta mostrando dati locali, separati dalla
VPS. Un elenco vuoto riguarda soltanto la cartella locale. Importare WEBSITE/INFO
non importa iscrizioni. Una copia autorizzata dei file esistenti può essere
collocata qui, preservandone struttura e permessi; la lettura ordinaria non riscrive il CSV; import e cancellazioni richiedono
l’abilitazione esplicita della scrittura. I dati restano esclusi da Git e dai pacchetti WEBSITE.

Flask non esegue PHP: questa semplificazione rende disponibile la gestione
dei file locali, non avvia un server PHP. Se si collauda già il RegForm con PHP
locale, impostare `MIFP_REGISTRATION_DIR` sulla sottocartella `registrations`
e `MIFP_UPLOAD_DIR` su `uploads` di questa stessa cartella privata. Non usare
la document root pubblica come destinazione degli invii.

`REGISTRATIONS_PRIVATE_ROOT` resta un override per un archivio esistente. In
produzione non viene creata una cartella vuota di ripiego: si leggono gli stessi
file PHP già presenti sulla VPS tramite i mount esistenti, read-only per
default e scrivibili solo con l’opt-in documentato. Non occorre
spostarli. Senza accesso configurato, l'elenco spiega la situazione (200), mentre
download e modifiche rimangono indisponibili (503).

Un percorso già configurato ma mancante resta un vero errore 503
(`not_connected`); permessi insufficienti restano `permission_denied`.

## Dashboard nuovamente in 503 dopo un invio

Se `registrations-access` risolve il problema ma il successivo invio lo
reintroduce, verificare sia il PHP pubblicato sia il pool attivo. Il regform
precedente può ripristinare `0700` sulle directory e `0600` sul CSV; il solo
collegamento Compose non corregge questo comportamento.

Controlli in sola lettura, senza stampare dati o credenziali:

```bash
sudo stat -c '%a %U:%G %n' \
  /srv/mifp-events-private/registrations/PLMCN-2027 \
  /srv/mifp-events-private/registrations/PLMCN-2027/registrations.csv
sudo grep -nF 'MIFP_REGISTRATION_READ_GROUP' \
  /srv/mifp-events/PLMCN-2027/regform/src/bootstrap.php
sudo grep -nF 'env[MIFP_REGISTRATION_READ_GROUP]' \
  /etc/php/8.3/fpm/pool.d/mifp-events.conf
```

Servono entrambi: il PHP deve usare il flag per mantenere directory `0750` e
CSV/allegati `0640`, e il pool deve esporre il flag con valore `1`. Il sorgente
aggiornato nell'Editor non aggiorna automaticamente il WEBSITE sulla VPS.

Se manca il supporto nel PHP, esportare dall'Editor il WEBSITE con
`regform/index.php` e `regform/src/bootstrap.php` aggiornati. Seguire la
procedura sopra: backup, revoca PHP, sostituzione revisionata del WEBSITE,
`host-apply php` se necessario, preparazione accesso e nuova approvazione.
Se manca solo il flag nel pool, non serve ripubblicare: applicare esplicitamente
`sudo mifpctl host-apply php` dal bundle aggiornato, poi predisporre l'accesso
a PHP revocato e riabilitarlo. Non correggere PHP direttamente mentre è
autorizzato e non usare chmod ricorsivi o una correzione periodica dei permessi.
Verificare la persistenza dei modi con dati sintetici nel runtime di test;
non creare ulteriori iscrizioni reali per diagnosticare il problema.

## Richieste, retry, cancellazione e recupero

La dashboard salva una richiesta con ID casuale sotto
`conferences/.registrations-admin/requests/`. Non dispone di sudo, di accesso
Docker o di un esecutore host. L'operatore esamina azione, conferenza e numero di
record; non viene mostrato il contenuto delle iscrizioni nel riepilogo host:

```bash
sudo mifpctl registrations-request REQUEST_ID
# Dopo revisione, applicare la richiesta scelta (delete o retry).
sudo mifpctl registrations-request REQUEST_ID --confirm PLMCN-2027 --approve SHA256_STAMPATO
```

Il primo comando di sola revisione termina senza applicare e con codice non-zero
finché manca la conferma. Il digest `--approve` lega l’approvazione al contenuto
revisionato: una richiesta modificata successivamente viene rifiutata. Per un retry agli organizzatori specificare anche
`--organizer-to INDIRIZZO_VERIFICATO`, preso dalle impostazioni dell'evento
revisionate; il destinatario non viene accettato dalla richiesta dashboard.
La conferma partecipante usa l'indirizzo della registrazione canonica. Il retry
host ricostruisce un messaggio testuale con riepilogo e receipt ID; agli
organizzatori allega la ricevuta. Non modifica i template del sito per i normali
invii. Sono consentiti al massimo 25 reinvii per richiesta, un canale per volta.
Ripetere una richiesta completata non invia altre email; nuove richieste non
possono riusare stati `accepted`, `sending` o `unknown`.

Per dati precedenti o esiti ambigui, **solo dopo aver ottenuto evidenza di mancata
accettazione** del messaggio dal trasporto, un operatore può attestarlo:

```bash
sudo mifpctl registrations-resolve PLMCN-2027 REG-YYYYMMDD-XXXXXXXX participant --not-accepted
```

Non usare questo comando come scorciatoia per uno stato sconosciuto: una conferma
accettata potrebbe essere già in viaggio. L'attestazione è auditata, non invia
email e non può annullare `accepted` o `not_requested`; abilita una successiva
richiesta di retry dalla dashboard. `registrations-access`, a PHP revocato,
prepara il lock anche per iscrizioni legacy.

Per eliminare prove selezionate o tutte le iscrizioni della conferenza, la
pagina richiede selezione e conferma testuale; la pulizia totale specifica
esplicitamente che ignora i filtri. Revocare PHP, poi revisionare e confermare
la richiesta sull'host. Vengono riletti CSV e ricevute canonici sotto lock:
richieste stale, duplicati, symlink e file arbitrari sono rifiutati. Prima della
prima modifica viene sincronizzato un TAR root-only dei record selezionati,
allegati e stati email in `/opt/mifp/registration-backups/REQUEST_ID.tar`.
Un backup fallito impedisce la cancellazione. Il digest SHA-256 nel journal
viene verificato prima di qualsiasi recupero. Il CSV mantiene il proprio inode
per restare coerente con il lock PHP; conserva intestazione e record rimanenti,
con normalizzazione esplicita della serializzazione CSV. Non vengono cancellate
intere directory né upload non associati ai record selezionati.

La richiesta conserva un journal root-only `started`/`completed`: dopo un errore
intermedio non viene ripetuta ciecamente. Ispezionare e recuperare dal backup.
Il recupero selettivo esplicito è:

```bash
sudo mifpctl registrations-request REQUEST_ID --restore --confirm PLMCN-2027
```

PHP deve restare revocato. Il recupero non sovrascrive receipt o allegati
esistenti, non elimina nuove iscrizioni e non riabilita PHP. In caso di restore
interrotto verificare i file già ripristinati prima di riprovare. I lock notifica
mancanti vengono ricreati prima di un retry, sotto il lock operativo.

I TAR locali delle pulizie completate seguono il numero di copie configurato
in `BACKUP_LOCAL_RETENTION` (default 14, minimo 2), applicato a ogni pulizia
completata. Quelli con journal incompleto restano disponibili per investigazione;
i piccoli journal senza dati personali restano per impedire replay. Questa
retention è **per operazioni**, non per giorni. I normali snapshot possono
contenere copie precedenti dei CSV e seguono la retention locale/restic già
configurata. Una cancellazione operativa non dichiara l'eliminazione immediata
di tutte le copie storiche. I metadati amministrativi restano nel sidecar per
riconciliazione/recupero: durata di note, storico e backup deve essere confermata
nella policy privacy dell'organizzatore. Non eseguire purge di snapshot o note
come effetto implicito di una selezione nella dashboard.

## Verifiche e rollback

Verificare autenticazione, CSRF, ricerca, filtri, ordinamento, paginazione,
contatori, export, download ricevuta e assenza di cache. Lo storage read-only
si verifica sulla Compose effettiva; non dare scrittura al container per risolvere
un errore di permessi. Nessun test richiede email o iscrizioni reali.
La consegna finale richiede evidenza dal provider/destinatario; `accepted` non
basta. Verificare separatamente organizer e participant, senza dedurre un esito
positivo per l'altro canale.

Dopo un restore completo degli snapshot, che conserva i modi privati più
restrittivi, predisporre nuovamente l’accesso con `registrations-access` per ogni
conferenza interessata, a PHP revocato, prima di riattivare la lettura dashboard.
Il sidecar amministrativo viene recuperato da `conferences/`.

In caso di regressione dashboard, disattivare
`REGISTRATIONS_DASHBOARD_ENABLED` con `config-set ... false`, quindi effettuare
restart o rollback immagine con gli strumenti esistenti. Conservare sidecar,
CSV, ricevute e journal. In caso di regressione WEBSITE, revocare PHP e usare il
rollback del pacchetto dalla dashboard; rivedere il codice ripristinato prima
di nuova autorizzazione. Se occorre ripristinare il pool, usare la copia verificata
e validare PHP-FPM; ripristinare il vecchio comando `--file=/etc/msmtprc`
reintrodurrebbe il difetto msmtp. Non annullare la correzione SMTP cambiando
credenziali o allargando i permessi.

Per l'Editor i pacchetti di riferimento possono essere indicati senza copiarli
nel repository o sostituire gli ZIP precedenti:

```bash
MIFP_TEST_WEBSITE_ZIP=/percorso/PLMCN-2027_WEBSITE.zip \
MIFP_TEST_INFO_ZIP=/percorso/PLMCN-2027_INFO.zip \
node --test tests/packages.test.cjs
python3 scripts/build-template.py --check
python3 -m unittest discover -s tests -p test_regform.py
```

Gli ZIP forniti e i successivi export restano ignorati da Git. Lo scaffold
`templates/conference-template.zip` dell'Editor è invece il distributable già
versionato, rigenerato dal suo `TEMPLATE/`; non è un pacchetto evento.


## Conferme editoriali e privacy ancora necessarie

Nel WEBSITE autorevole fornito il form risulta aperto (`submit_enabled: true`)
e richiede 16 MB nel pacchetto; il runtime limita correttamente la promessa a
10 MB. Le impostazioni dell'evento sono state preservate, mentre il template
per nuove conferenze resta inizialmente chiuso. Prima della ripubblicazione
l'organizzatore deve confermare apertura, scadenze early bird/regular/refund,
nota fiscale e note di registrazione ancora TBC in `conference.yaml`.

Il testo privacy cita MIFP, ER GLOBAL COMMUNICATION SRL e il riferimento
«law N° 163/03». Confermare riferimento, ruoli e responsabilità effettivi,
finalità, condizioni di registrazione e durata di conservazione di CSV,
ricevute, note amministrative, email e backup. La correzione tecnica non
sostituisce queste decisioni e non introduce testi legali o prezzi definitivi.


### Ripristino manuale dalla dashboard

L'export **Recovery ZIP + originals** e **Import registrations** sono azioni
separate dall'import WEBSITE/INFO. Lo ZIP conserva CSV e ricevute originali
soltanto nel download richiesto dall'operatore, senza backup automatici sul
server. L'import richiede password, conferma, alert amministratore e gli stessi
permessi di scrittura previsti per la cancellazione; aggiunge record mancanti
senza sovrascrivere quelli esistenti o inviare email ai partecipanti.
Vedere la [procedura e i limiti](regform-dashboard-update.md#export-e-import-manuale-delle-registrazioni).
