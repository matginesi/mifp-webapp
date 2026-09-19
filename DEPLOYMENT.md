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

## I comandi da ricordare

Dopo la prima installazione, per la manutenzione ordinaria bastano:

```bash
sudo mifpctl update-check          # read-only: confronta current con :latest
sudo mifpctl update                # risolve :latest -> digest e deploya in sicurezza
sudo mifpctl status
sudo mifpctl logs
sudo mifpctl rollback              # torna alla release precedente
```

Per una release specifica o per audit resta disponibile:

```bash
sudo mifpctl deploy sha-<commit>   # deploy esplicito; NON modifica il DB
```

Se qualcosa non torna:

```bash
sudo mifpctl doctor
```

Audit read-only di superficie, permessi e isolamento:

```bash
sudo mifpctl security-check
```

Backup manuale immediato:

```bash
sudo mifpctl backup
```

## VPS configuration lifecycle

Il lifecycle è deliberatamente progressivo:

```text
bootstrap -> configure -> config-check -> init -> doctor
                                                               -> deploy
                                                               -> backup/restore
```

`bootstrap-vps.sh` prepara Ubuntu, Docker, Caddy, PHP-FPM, utenti, firewall e
file iniziali; non richiede dominio o admin. `configure` conserva i valori che
non vengono modificati. `init` è ammesso solo quando `config-check` conferma
domini, admin, DNS (fuori dal local mode) e login registry.

## Prima installazione, una volta sola

Per la procedura completa su una VPS vuota (DNS, chiavi SSH, hardening SSH,
firewall, segreti, GHCR, primo deploy, backup, smoke test di disaster recovery e
checklist finale) usa il runbook dedicato:

**[docs/DEPLOY_NEW_VPS.md](docs/DEPLOY_NEW_VPS.md)**

Prerequisiti: Ubuntu e accesso SSH con `sudo`. Il DNS può essere completato
anche dopo il bootstrap.

Dal PC:

```bash
scp -r deploy user@host:/tmp/mifp-deploy
ssh user@host
```

Sulla VPS:

```bash
sudo bash /tmp/mifp-deploy/bootstrap-vps.sh \
  --image-repository ghcr.io/matginesi/mifp-webapp
```

Il bootstrap installa Docker/Caddy/SQLite/PHP-FPM, crea `/opt/mifp`, configura firewall,
systemd e `mifpctl`, quindi termina anche con configurazione incompleta. Finché
il dominio manca, Caddy risponde su HTTP con una pagina di attesa; non tenta
ACME e non inventa record locali.

Configura poi, anche in più sessioni:

```bash
sudo mifpctl configure
sudo mifpctl config-set DOMAIN mifp.eu
sudo mifpctl config-set PUBLIC_IPV4 203.0.113.10
sudo mifpctl admin
sudo mifpctl config-show
```

I non-segreti sono in `/etc/mifp/config.env` (`root:root`, `0640`); hash e
password in `/etc/mifp/secrets.env` (`0600`). `config-set` rifiuta i segreti in
command line: usa `configure --section mail|backup`. Il PAT GHCR rimane soltanto
nel credential store Docker di root.

La CI esegue in parallelo hygiene, secret scan, test e audit delle dipendenze; la
build/publish GHCR parte solo se tutti i gate sono verdi. Il workflow automatico
reagisce **solo ai push su `main`**; `workflow_dispatch` resta disponibile per un
controllo manuale. Il repository non contiene Dependabot e nessun workflow crea
branch, PR, issue o commit. Il cleanup GHCR è manuale, non schedulato. Ogni job
ha un `timeout-minutes` esplicito, i comandi di rete più delicati hanno retry +
timeout e la concurrency cancella i run obsoleti sullo stesso ref: un runner
bloccato non può restare appeso indefinitamente. Dopo che GitHub Actions ha
pubblicato una release:

```bash
sudo mifpctl config-check
sudo mifpctl init
sudo mifpctl doctor
```

Il package `ghcr.io/matginesi/mifp-webapp` è pubblico: `config-check` verifica
prima il manifest anonimamente e `init` esegue normalmente un pull anonimo.
`registry-login` rimane disponibile soltanto per eventuali package privati; il
PAT passa a `docker login` via stdin e non viene salvato nei file MIFP. `init`
scarica `ghcr.io/matginesi/mifp-webapp:latest`, lo risolve nel digest OCI
immutabile, crea un DB **schema-only v10**, lo verifica e avvia la webapp. Lo
stato persistente non contiene mai `latest`.
`config-check` non modifica alcun file. SMTP e backup remoto mancanti sono
opzionali; dominio/admin/DNS e accesso all'immagine sono bloccanti. `doctor` verifica invece
l'host e, dopo `init`, anche DB, release e healthcheck.
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

Per importare il backup storico completo della vecchia document root, esegui prima
il preflight read-only e poi l'import:

```bash
sudo mifpctl events-check /path/al/backup/events.mifp.eu
sudo mifpctl events-import /path/al/backup/events.mifp.eu
```

Passa **la document root pubblica degli eventi**, non un backup completo dell'hosting.
Il preflight rifiuta symlink, file speciali, mount annidati e payload che non devono
mai entrare nel tree pubblico (`.env`, repository `.git`, database/dump SQL, chiavi,
credential file e vecchi dati di registrazione). Segnala inoltre file PHP e riferimenti
residui a `old.mifp.eu` senza eseguirli o modificarli.

L'import riesegue lo stesso preflight come gate, copia in staging, normalizza i permessi e sostituisce
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

Flusso normale dopo `git push origin main` e GitHub Actions verde:

```bash
sudo mifpctl update-check
sudo mifpctl update
sudo mifpctl status
```

`update-check` non fa pull, restart o scritture di stato. `update` usa `:latest`
solo per scoprire il digest candidato e poi passa quel digest immutabile alla
stessa pipeline del deploy esplicito. Per scegliere una release precisa:

```bash
sudo mifpctl deploy sha-<commit>
```

Il motore di deploy:

1. valida configurazione, spazio e DB corrente;
2. scarica il tag `sha-*` e lo fissa al digest OCI reale `@sha256:...`;
3. crea una snapshot SQLite temporanea leggibile dal container non-root e prova la nuova immagine contro quella copia **prima dello switch**;
4. avvia la nuova release e attende `/ready`;
5. se serve un rollback automatico, verifica anche che la release precedente torni realmente `ready`;
6. registra `CURRENT_IMAGE`/`PREVIOUS_IMAGE` solo dopo il successo;
7. conserva localmente corrente e precedente, pulendo vecchie immagini MIFP.

`latest` è unicamente un canale di discovery per `init`, `update-check` e
`update`; `release.env` non lo persiste mai. `deploy` continua a rifiutare
`latest` e ogni altro tag mutabile. Le operazioni che modificano la release
(`update`, `deploy`, rollback) sono protette da `flock`, quindi non possono
sovrapporsi.

## Test locale con `.home.arpa`

Per una VM con hostname Linux `vpsbox` e dominio applicativo locale distinto:

```bash
sudo bash /tmp/mifp-deploy/bootstrap-vps.sh \
  --domain vpsbox.home.arpa \
  --image-repository ghcr.io/matginesi/mifp-webapp
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
# alias esplicito:
sudo mifpctl admin-reset-password
```

La password viene chiesta due volte e resta soltanto sotto forma di hash. Per
configurare SMTP o restic senza esporre password nella shell history:

```bash
sudo mifpctl configure --section mail
sudo mifpctl configure --section backup
```

Non scrivere password in chiaro nel repository, in `config.env` o nel file
runtime `/opt/mifp/.env`.

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

## Production security checklist

Prima di considerare una VPS pronta:

```text
[ ] sudo mifpctl doctor -> Doctor: OK
[ ] sudo mifpctl security-check -> Security check: OK
[ ] solo SSH, 80 e 443 sono listener pubblici attesi (v4 e v6)
[ ] sudo mifpctl ssh-harden --operator <utente> applicato e provato da una
    nuova sessione (sshd -T: PasswordAuthentication no)
[ ] unattended-upgrades attivo e Automatic-Reboot false;
    /var/run/reboot-required assente
[ ] Caddyfile valido e backend Flask solo su 127.0.0.1:8000
[ ] container non-root, rootfs read-only, no-new-privileges, niente docker.sock
[ ] /etc/mifp/secrets.env è 0600; Docker config root è 0600 se presente
[ ] /etc/mifp è custodito fuori dalla VPS (escrow dei segreti)
[ ] PHP eventi è deny-by-default e la allow-list è stata revisionata
[ ] backup locale riuscito e restore provato almeno una volta
[ ] backup off-site verificato, se configurato
```

La checklist operativa completa, con i comandi di verifica, è in
[docs/DEPLOY_NEW_VPS.md](docs/DEPLOY_NEW_VPS.md).

`security-check` è diagnostico e non modifica la macchina. Verifica la policy SSH
**effettiva** (`sshd -T`), che UFW sia attivo con le regole IPv4 e IPv6 attese, i
permessi dei file, i listener pubblici inaspettati, l'isolamento del container, la
freschezza e l'integrità dell'ultimo backup, e che le credenziali di backup non
siano esposte al container web.

## GHCR privato

Solo per un registry o package privato che risponde `denied`/`unauthorized`:

```bash
sudo mifpctl registry-login
```

Usa un PAT classic con solo `read:packages`. Se un pull restituisce `denied`,
`unauthorized` o `authentication required`, `mifpctl` mostra il comando da
eseguire e non registra alcuna release parziale.

Ulteriori dettagli: [installazione VPS/VM](docs/deployment/vps-installation.md),
[backup](docs/deployment/backups.md),
[hardening](docs/deployment/hardening.md), [schema DB](docs/database-schema.md).
