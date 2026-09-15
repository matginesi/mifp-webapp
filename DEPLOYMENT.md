# Deploy MIFP

Produzione usa un solo percorso:

```text
GitHub Actions -> GHCR -> mifpctl sulla VPS -> Docker -> Caddy -> Flask/SQLite
                                           \-> events.mifp.eu -> file statici (+ PHP-FPM opt-in)
```

La VPS non compila codice, non esegue scraper e non migra il database durante
l'avvio. Non serve un virtualenv Python sulla VPS.

Per l'installazione completa, inclusi DNS, VirtualBox, `.home.arpa`, trust della
CA locale e troubleshooting, segui la
[guida passo-passo VPS pubblica e VM](docs/deployment/vps-installation.md).

## I 4 comandi da ricordare

Dopo la prima installazione, normalmente bastano:

```bash
sudo mifpctl deploy sha-<commit>   # nuova versione; NON modifica il DB
sudo mifpctl status
sudo mifpctl logs
sudo mifpctl rollback              # torna alla release precedente
```

Se qualcosa non torna:

```bash
sudo mifpctl doctor
```

Backup manuale immediato:

```bash
sudo mifpctl backup
```

## Prima installazione, una volta sola

Prerequisiti: Ubuntu, DNS del dominio puntato alla VPS, accesso SSH con `sudo`.

Dal PC:

```bash
scp -r deploy user@host:/tmp/mifp-deploy
ssh user@host
```

Sulla VPS:

```bash
sudo bash /tmp/mifp-deploy/bootstrap-vps.sh \
  --domain mifp.eu \
  --image-repository ghcr.io/matginesi/mifp-webapp
```

Il bootstrap installa Docker/Caddy/SQLite/PHP-FPM, crea `/opt/mifp`, configura firewall,
HTTPS, backup automatico, `SECRET_KEY` e amministratore. La password admin viene
chiesta due volte, deve avere almeno **10 caratteri** e, in caso di errore o
mancata corrispondenza, viene richiesta di nuovo senza interrompere il bootstrap.
Viene salvato solo l'hash. `/opt/mifp/.env` è `root:root` con permessi `0600`.

La CI esegue in parallelo test e audit delle dipendenze; la build/publish GHCR
parte solo se entrambi sono verdi. Dopo che GitHub Actions ha pubblicato una release:

```bash
sudo mifpctl registry-login
sudo mifpctl init
sudo mifpctl doctor
```

`registry-login` richiede username GitHub e PAT classic nascosto con scope minimo
`read:packages`; il PAT passa a `docker login` via stdin e non viene salvato nei
file MIFP. `init` scarica `ghcr.io/matginesi/mifp-webapp:latest`, lo risolve nel digest OCI
immutabile, crea un DB **schema-only v10**, lo verifica e avvia la webapp. Lo
stato persistente non contiene mai `latest`.
`mifpctl doctor` può essere eseguito anche subito dopo il bootstrap: prima di
`init`, `DB: NOT INITIALIZED` e `Release: NOT INITIALIZED` descrivono lo stato
atteso e non sono errori. Dopo `init`, DB, release e healthcheck diventano
obbligatori.
Apri quindi la dashboard e importa lo ZIP prodotto dagli scraper. Sono accettati
solo package moderni esplicitamente versionati (`mifp-content` v1 oppure
`mifp-jsonl-v2` v2); vecchi ZIP e vecchi JSONL self-contained sono rifiutati.


## `events.mifp.eu`: vecchi eventi e nuove conferenze

`events.mifp.eu` non passa da Flask. Caddy serve direttamente:

```text
/opt/mifp/events/
  PLMCN-2025/
  ICP2DC-2024/
  ...
```

Per importare il backup storico completo della vecchia document root:

```bash
sudo mifpctl events-import /path/al/backup/events.mifp.eu
```

L'import rifiuta symlink, copia in staging, normalizza i permessi e sostituisce
`/opt/mifp/events` con un rename atomico. Prima dello switch azzera sempre la
allow-list PHP: un nuovo tree non eredita mai codice eseguibile dal precedente.
Il tree precedente resta in `/opt/mifp/events.previous` e può essere scambiato
di nuovo con:

```bash
sudo mifpctl events-rollback
```

Anche il rollback disabilita PHP per tutti i path; se la versione ripristinata
ha davvero bisogno di PHP, il relativo prefix va riabilitato esplicitamente.

Quindi la migrazione storica non richiede conversioni: `PLMCN-2025/index.html`
continua a rispondere come `https://events.mifp.eu/PLMCN-2025/`, preservando il
casing originale.

### PHP: installato, ma deny-by-default

Il bootstrap installa un pool PHP-FPM dedicato (`mifp-events`) sul socket
`/run/php/mifp-events.sock`. **Nessun `.php` del backup storico viene eseguito o
servito come sorgente**: Caddy risponde 404 finché un path non viene abilitato
esplicitamente.

Per una futura conferenza con form PHP:

```bash
sudo mifpctl events-php-enable PLMCN-2027/regform
sudo mifpctl events-php-list
```

Per disabilitarlo:

```bash
sudo mifpctl events-php-disable PLMCN-2027/regform
```

Lo storage scrivibile di PHP è separato dal document root:
`/opt/mifp/events-private/`. Un nuovo form deve quindi salvare registrazioni,
upload e sessioni lì (per esempio
`/opt/mifp/events-private/registrations/PLMCN-2027/`), non dentro il sito
pubblico. `regform/settings.yaml`, `regform/src/` e `regform/registrations/`
sono comunque negati da Caddy.

PHP-FPM non configura da solo la consegna e-mail: per un futuro form che invia
mail va scelta esplicitamente una configurazione SMTP/MTA o un provider.

## Tre cicli separati

### 1. Nuovo codice

```bash
sudo mifpctl deploy sha-<commit>
```

Il deploy:

1. valida configurazione, spazio e DB corrente;
2. scarica il tag `sha-*` e lo fissa al digest OCI reale `@sha256:...`;
3. crea una snapshot SQLite temporanea leggibile dal container non-root e prova la nuova immagine contro quella copia **prima dello switch**;
4. avvia la nuova release e attende `/ready`;
5. se serve un rollback automatico, verifica anche che la release precedente torni realmente `ready`;
6. registra `CURRENT_IMAGE`/`PREVIOUS_IMAGE` solo dopo il successo;
7. conserva localmente corrente e precedente, pulendo vecchie immagini MIFP.

`latest` è ammesso soltanto dal comando iniziale `init` come selector transitorio;
`deploy` rifiuta `latest` e ogni altro tag mutabile. Il deploy è protetto da `flock`,
quindi due operazioni di manutenzione non possono sovrapporsi.

## Test locale con `.home.arpa`

Per una VM con hostname Linux `vpsbox` e dominio applicativo locale distinto:

```bash
sudo bash /tmp/mifp-deploy/bootstrap-vps.sh \
  --domain vpsbox.home.arpa \
  --image-repository ghcr.io/matginesi/mifp-webapp
sudo mifpctl registry-login
sudo mifpctl init
sudo mifpctl doctor
```

Solo per un dominio che termina esattamente in `.home.arpa`, il bootstrap:

- mantiene un blocco marcato e idempotente in `/etc/hosts` con loopback per il
  dominio, `www` ed `events`, così i check eseguiti dalla VM si autorisolvono;
- configura `tls internal`, prova `caddy trust` sulla sola VPS e indica il file
  della root CA da importare manualmente sulla workstation;
- stampa la riga `/etc/hosts` della workstation usando l'IP LAN se univoco.

Questa è esclusivamente una compatibilità per test locale. Domini pubblici come
`mifp.eu` non ricevono entry locali né `tls internal`: seguono DNS pubblico e
ACME normale. Il trust della workstation non viene mai modificato da remoto.

`vpsbox` è l'hostname Linux; `vpsbox.home.arpa` è il dominio applicativo. Sono
concetti distinti.

### 2. Nuovi contenuti

```text
scraper locale -> MIFP_IMPORT.zip -> Dashboard -> Import
```

**Non** copiare o `rsync`-are `mifp.db` sopra il DB vivo. L'import è
transazionale e conservativo: aggiorna/aggiunge i dati presenti nel package, ma
non cancella implicitamente record, link o asset locali omessi dal package.

### 3. Nuovo schema DB (raro)

Il runtime non migra mai il DB automaticamente. Per una futura release che
richiede un nuovo schema, prepara una copia localmente:

```bash
./mifp db-upgrade-copy OLD.db NEW.db
./mifp db-check NEW.db
```

Poi sulla VPS:

```bash
sudo mifpctl upgrade-db sha-<commit> /path/NEW.db
```

La VPS verifica nuova immagine + DB candidato, crea un backup del DB vivo,
ferma il servizio solo per lo swap e ripristina DB+release precedenti se la
nuova coppia non torna ready.

I DB storici non vengono più "riparati" automaticamente: per dati precedenti a
v9 crea un DB corrente e rigenera/converti i contenuti in un package moderno
`mifp-content` v1 o `mifp-jsonl-v2` v2 prima dell’import.

## Rollback

```bash
sudo mifpctl rollback
```

`release.env` contiene digest OCI immutabili. Il rollback usa prima l'immagine
locale, quindi continua a funzionare anche se GHCR o Internet sono
momentaneamente indisponibili. Il normale rollback applicativo non modifica il
DB.

## Backup e restore

Un timer systemd esegue automaticamente `deploy/backup.sh`. I backup host-only
sono snapshot point-in-time complete sotto `/var/backups/mifp/snapshots/`:

```text
snapshot-YYYYMMDD-HHMMSS-NNNNNNNNN/
  mifp.db
  mifp.db.sha256
  manifest.json
  assets/
  conferences/
  config/
  events/
  events-private/
  events-php-enabled.txt
  README.txt
```

Il container viene brevemente messo in pausa durante la fotografia dei file;
SQLite viene copiato con la Backup API e verificato. `manifest.json` registra
l'SHA-256 dell'intero insieme ripristinabile (`mifp.db`, `assets/`, `conferences/`,
`config/`, `events/`, `events-private/`, `events-php-enabled.txt`) e il restore rifiuta file mancanti, extra, alterati o symlink. Le
snapshot successive usano hardlink per i file invariati. Backup immediato:

```bash
sudo mifpctl backup
```

Restore del solo DB corrente:

```bash
sudo mifpctl restore-db /var/backups/mifp/snapshots/snapshot-.../mifp.db
```

Restore dell'intera fotografia DB + file:

```bash
sudo mifpctl restore-snapshot /var/backups/mifp/snapshots/snapshot-...
```

Entrambi pre-validano il DB e non effettuano alcuno swap se il servizio non
si arresta correttamente. Il restore completo verifica prima anche il manifest
di integrità; se la webapp non torna ready, viene ripristinata la fotografia
precedente. Per disaster recovery conserva anche una copia **off-site**;
se configuri restic, ogni snapshot completata viene replicata cifrata.

## Admin e segreti

Cambio password:

```bash
sudo mifpctl admin
```

Riconfigurazione guidata:

```bash
sudo mifpctl configure
sudo mifpctl configure --admin
```

Non scrivere password in chiaro nel repository o in `/opt/mifp/.env`.

## Sicurezza runtime

```text
Internet -> Caddy :443 -> 127.0.0.1:8000 -> container
```

- `/ready` è accessibile solo localmente;
- `/health` pubblico espone solo lo stato essenziale;
- container UID/GID `10001:10001`;
- root filesystem read-only;
- `cap_drop: ALL`, `no-new-privileges`;
- limiti RAM/CPU/PID;
- SQLite ha un solo worker Gunicorn in produzione.

## GHCR privato

Una volta sola:

```bash
sudo mifpctl registry-login
```

Usa un PAT classic con solo `read:packages`. Se un pull restituisce `denied`,
`unauthorized` o `authentication required`, `mifpctl` mostra il comando da
eseguire e non registra alcuna release parziale.

Ulteriori dettagli: [installazione VPS/VM](docs/deployment/vps-installation.md),
[backup](docs/deployment/backups.md),
[hardening](docs/deployment/hardening.md), [schema DB](docs/database-schema.md).
