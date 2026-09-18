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

`public_path` viene quindi preservato esattamente. Questo evita regressioni durante la futura
migrazione di `events.mifp.eu` sulla VPS.

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
- assenza di `regform/registrations/`, che è runtime data privata e non deve entrare in un
  source package.

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

In questa fase l'import di un package editor valido arriva a `staged`. La pubblicazione vera e
propria su `events.mifp.eu` verrà aggiunta separatamente, così un upload non può diventare
eseguibile o pubblico per il solo fatto di essere stato importato.

## Hosting VPS

Il publish pubblico è volutamente più semplice dello storage di authoring:

```text
Internet
  -> Caddy :443
       -> mifp.eu / www.mifp.eu -> Flask :8000
       -> events.mifp.eu        -> /opt/mifp/events/ (file server)
                                    \-> PHP-FPM solo per prefix esplicitamente abilitati
```

I siti storici possono quindi essere copiati 1:1 nella document root con
`mifpctl events-import`; non devono essere convertiti in un package editor per
essere pubblicati. Prima dell'import usa `mifpctl events-check <document-root>`:
il preflight rifiuta materiale privato/secret-like e oggetti filesystem non sicuri,
e l'import riesegue automaticamente lo stesso gate. I package `mifp-conference-editor` conservati sotto
`data/conferences/` restano invece sorgenti/staging della dashboard e possono
essere pubblicati nello stesso filesystem in una fase successiva.

PHP è deny-by-default: i `.php` storici restituiscono 404. Il bootstrap prepara
un pool FPM dedicato e `/opt/mifp/events-private`, ma l'esecuzione si abilita
soltanto con `mifpctl events-php-enable <public-prefix>`. Un import completo o
`events-rollback` svuota sempre la allow-list, così il nuovo tree non eredita
codice eseguibile dal precedente. Le registrazioni non devono mai vivere sotto
`/opt/mifp/events`.
