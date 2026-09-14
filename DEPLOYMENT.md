# Deploy MIFP

Produzione usa un solo percorso:

```text
GitHub Actions -> GHCR -> mifpctl sulla VPS -> Docker -> Caddy -> Flask/SQLite
```

La VPS non compila codice, non esegue scraper e non migra il database durante
l'avvio. Non serve un virtualenv Python sulla VPS.

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
  --image-repository ghcr.io/OWNER/REPO
```

Il bootstrap installa Docker/Caddy/SQLite, crea `/opt/mifp`, configura firewall,
HTTPS, backup automatico, `SECRET_KEY` e amministratore. La password admin viene
chiesta due volte, deve avere almeno **10 caratteri** e viene salvato solo
l'hash. `/opt/mifp/.env` è `root:root` con permessi `0600`.

La CI esegue in parallelo test e audit delle dipendenze; la build/publish GHCR
parte solo se entrambi sono verdi. Dopo che GitHub Actions ha pubblicato una release:

```bash
sudo mifpctl first-deploy sha-<commit>
```

`first-deploy` crea un DB **schema-only v9**, lo verifica e avvia la webapp.
Apri quindi la dashboard e importa lo ZIP prodotto dagli scraper. Sono accettati
solo package moderni esplicitamente versionati (`mifp-content` v1 oppure
`mifp-jsonl-v2` v2); vecchi ZIP e vecchi JSONL self-contained sono rifiutati.

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

`latest` e altri tag mutabili sono rifiutati. Il deploy è protetto da `flock`,
quindi due operazioni di manutenzione non possono sovrapporsi.

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
  README.txt
```

Il container viene brevemente messo in pausa durante la fotografia dei file;
SQLite viene copiato con la Backup API e verificato. `manifest.json` registra
l'SHA-256 dell'intero insieme ripristinabile (`mifp.db`, `assets/`, `conferences/`,
`config/`) e il restore rifiuta file mancanti, extra, alterati o symlink. Le
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
docker login ghcr.io
```

Usa un token con solo `read:packages`.

Ulteriori dettagli: [backup](docs/deployment/backups.md),
[hardening](docs/deployment/hardening.md), [schema DB](docs/database-schema.md).
