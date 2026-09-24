# Conference sites

## Responsabilità

La webapp MIFP non deve diventare un secondo editor di conferenze.

- `events` è il catalogo istituzionale: titolo, date, descrizione, relazioni e URL pubblico.
- `conference_sites` è il record operativo del microsito: path pubblico, origine del pacchetto,
  versione, checksum e stato di deploy.
- `mifp-conference-editor` è l'authoring tool per i nuovi micrositi.

Il collegamento è `conference_sites.event_id -> events.id` (`ON DELETE SET NULL`). Il vincolo
univoco impedisce che lo stesso evento istituzionale venga collegato per errore a due micrositi.

## Identità e URL

`slug` e `public_path` non sono sinonimi.

```text
slug        = plmcn-2025       # storage interno, normalizzato
public_path = PLMCN-2025       # URL storico, case-sensitive
```

`public_path` viene quindi preservato esattamente per mantenere compatibili i
link pubblici di `events.mifp.eu`, indipendentemente dal backend di pubblicazione.

## Pacchetti da mifp-conference-editor

La dashboard riconosce il contratto corrente dell'editor tramite almeno:

```text
conference.yaml
conference.version.json
data/people.csv
data/program.csv
index.html
```

La versione supportata è `schema = 1`. L'import legge soltanto metadati necessari per il
catalogo operativo; non rigenera il sito e non trasforma il pacchetto nel formato del vecchio
builder interno.

Durante l'import vengono verificati:

- ZIP traversal e path assoluti;
- symlink ed entry cifrate;
- numero di file, dimensione espansa, dimensione per file e compression ratio;
- schema/versione dei metadata;
- intestazioni CSV richieste;
- PHP consentito soltanto sotto `regform/`;
- assenza di dati runtime privati sotto `regform/registrations/`; sono ammessi soltanto i
  piccoli guard file pubblici del Conference Editor (`.gitignore`, `.htaccess` deny-only,
  `index.php` 404-only e placeholder `.gitkeep`/`.keep`).

Il SHA-256 dell'intero ZIP viene registrato nel database.

## Storage

Ogni workspace usa il layout:

```text
conferences/<slug>/
  assets/                  # solo builder interno/legacy
  packages/
    <sha256>.zip           # pacchetto editor originale, immutabile
  sources/
    <sha256>/              # sorgente validata e normalizzata per un futuro publish
```

Un nuovo import non sovrascrive il precedente. Reimportare gli stessi byte è idempotente.
Il download dalla dashboard rilegge il package immutabile e ne verifica nuovamente il checksum.

## Stato di deploy

`deploy_status` è distinto dallo stato editoriale del record:

```text
unpublished -> staged -> published
                    \-> failed
```

La dashboard **Conference sites** conserva e valida i package dei micrositi. Nel
wizard di import, `WEBSITE` e `INFO` sono indipendenti: WEBSITE gestisce la copia
applicativa del package, INFO crea/aggiorna il record Event. Questo stato non
espone la scelta del backend nella normale UI evento.

## Hosting pubblico e transizione

La produzione corrente usa il backend locale della VPS:

```text
Internet
  -> VPS Caddy :443 -> mifp.eu / www.mifp.eu -> Flask :8000
  -> VPS Caddy :443 -> events.mifp.eu -> /srv/mifp-events
                                      -> PHP-FPM dedicato (solo regform approvati)
```

Questa è la Phase 1 (`EVENTS_PUBLISH_BACKEND=local-vps`). Il publisher scrive
solo il `public_path` selezionato. Caddy nega dot/stage/rollback, file sensibili
e ogni file PHP-like. L'operatore può autorizzare esclusivamente un path
`<public_path>/regform`; i `.php` sotto quel prefisso passano al pool dedicato,
mentre PHTML/PHAR e PHP fuori allow-list restano 404. Registrazioni, sessioni e
upload vivono fuori dal document root in `/srv/mifp-events-private`.

La Phase 2 è futura e richiede prima la riparazione dell'hosting Aruba: backend
remoto/FTPS, DNS eventi verso Aruba e nessun virtual host eventi sulla VPS. Il
modello DB non cambia. Prima del cutover l'operatore deve però definire e
verificare sul provider remoto backup, retention e prova di restore delle
registrazioni/upload autorevoli: le snapshot VPS non possono proteggerli.

## Contratto runtime dei regform PHP

L'allow-list autorizza l'esecuzione, non adatta il codice storico. Ogni regform
approvato deve usare esplicitamente le directory comunicate dal pool:

```text
MIFP_EVENTS_PRIVATE_DIR=/srv/mifp-events-private
MIFP_REGISTRATION_DIR=/srv/mifp-events-private/registrations
MIFP_UPLOAD_DIR=/srv/mifp-events-private/uploads
```

Il document root pubblico appartiene all'utente applicativo e al gruppo di
lettura eventi (`0750`); il processo PHP dedicato può leggerlo ma non scriverlo.
Le directory private appartengono esclusivamente all'utente `mifp-events` e
usano `0700`; i file ripristinati vengono normalizzati a `0600`. Sessioni e
temporanei usano le rispettive sottodirectory private e non sono autorevoli.

Un form deve quindi leggere `MIFP_REGISTRATION_DIR`, scrivere lì le submission
e usare `MIFP_UPLOAD_DIR` per gli upload persistenti. Non deve creare database,
CSV, upload o file di configurazione nel tree pubblico. Per mantenere lo stesso
package utilizzabile anche su hosting condiviso Aruba, un regform può adottare
un fallback esplicito: usa le directory `MIFP_*` quando sono presenti (VPS),
altrimenti il proprio storage locale protetto da `.htaccess` (hosting remoto).
Le credenziali SMTP non fanno parte del package: i regform continuano a usare
`mail()` e, in `local-vps`, PHP-FPM lo instrada attraverso il relay host gestito
da `sudo mifpctl configure --section mail`. Il repository include
`TESTS/fixtures/regform_contract/index.php` come esempio eseguibile minimo;
nessun PHP caricato viene riscritto automaticamente. Ogni form storico reale
deve essere verificato separatamente prima dell'abilitazione.

## Versioning and rollback

Conference WEBSITE imports record the current package version (from
`conference.version.json` when present), the package SHA-256 and package schema
version in `conference_sites`.  The package manifest also retains one bounded
snapshot of the immediately previous imported WEBSITE version.

When the import wizard keeps its rollback copy, **Dashboard → Conference sites**
shows both current and previous versions and exposes **Restore previous**.  The
restore swaps only the public WEBSITE directory and package metadata; it does
not modify the canonical Event record created from `_INFO.zip`. PHP remains
operator-controlled and deny-by-default; publishing or restoring a package
never adds its regform to the allow-list.

`events-republish-all` recupera esclusivamente `source_format=legacy-static`:
è l'unico formato che il workflow WEBSITE pubblica tramite `EventSitePublisher`.
I package `conference-editor` sono conservati immutabili con stato `staged` e
restano scaricabili, ma il workflow corrente non li pubblica; `internal` resta
`unpublished`. Questa distinzione è coperta dai test e non modifica
`public_path`.
