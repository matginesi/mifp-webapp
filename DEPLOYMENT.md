# Deploy MIFP

Produzione usa un solo percorso:

```text
GitHub Actions -> GHCR -> mifpctl sulla VPS -> Docker -> Caddy -> Flask/SQLite

events.mifp.eu -> Caddy statico/PHP-FPM dedicato sulla VPS (Phase 1)
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

Da un checkout locale indipendente dalla VPS, verifica inoltre la superficie
pubblica dopo ogni rilascio:

```bash
./mifp security production https://www.mifp.eu --strict
```

Il controllo esterno non autentica, non modifica dati e non esegue crawling.
Un target irraggiungibile termina con codice `2` e stato `UNKNOWN`, distinto da
un finding di sicurezza.

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

`bootstrap-vps.sh` prepara Ubuntu, Docker, Caddy, utenti, firewall e file
iniziali; configura e avvia il pool PHP-FPM eventi soltanto per `local-vps`.
Non richiede dominio o admin. `configure` conserva i valori che
non vengono modificati. `init` è ammesso solo quando `config-check` conferma
domini, admin, DNS (fuori dal local mode) e login registry.

## Prima installazione, una volta sola

Per la procedura completa su una VPS vuota (DNS, policy SSH, firewall,
segreti, GHCR, primo deploy, backup, smoke test di disaster recovery e
checklist finale) usa il runbook dedicato:

**[docs/DEPLOY_NEW_VPS.md](docs/DEPLOY_NEW_VPS.md)**

Prerequisiti: Ubuntu 22.04 o 24.04 e accesso SSH con `sudo`. Il target reale è
Ubuntu 24.04; il bootstrap rifiuta esplicitamente altre release.

### Primo cutover diretto, senza staging

Per questa prima produzione la sequenza prevista è:

```text
repository/CI verde
-> DNS di mifp.eu e www.mifp.eu verso la VPS
-> bootstrap immediato con --domain mifp.eu
-> configurazione applicazione/admin
-> config-check -> init -> doctor -> security-check
-> verifica finale HTTPS, DNS e backup
```

Il cambio DNS prima che l'applicazione sia avviata può causare una breve
indisponibilità di cutover; per questo rilascio è accettata. Configura dapprima
IPv4 (`A` per l'apex e `A`/`CNAME` per `www`). Aggiungi record
`AAAA` soltanto dopo aver verificato raggiungibilità IPv6 e comportamento UFW
su IPv6. Non è previsto alcun dominio di staging.

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

Il bootstrap installa Docker/Caddy/SQLite e i pacchetti PHP, crea `/opt/mifp`,
configura firewall, systemd e `mifpctl`; il servizio eventi PHP viene attivato
soltanto per `local-vps`. Il supporto a un bootstrap senza dominio resta
utile per installazioni non ancora configurate; il cutover reale usa invece
`--domain mifp.eu` subito dopo il cambio DNS, così Caddy segue il normale
percorso pubblico DNS/ACME.

Configura poi, anche in più sessioni:

```bash
sudo mifpctl configure
sudo mifpctl config-set DOMAIN mifp.eu
sudo mifpctl config-set PUBLIC_IPV4 203.0.113.10
sudo mifpctl admin
sudo mifpctl config-show
```

I non-segreti sono in `/etc/mifp/config.env` (`root:root`, `0640`); hash e
password in `/etc/mifp/secrets.env` (`0600`). Questo file resta la fonte
canonica: `mifpctl` materializza in `/etc/mifp/secrets/` (`0700`) quattro file
Docker root-only (`0600`) per `SECRET_KEY`, hash admin, SMTP e publisher remoto.
I secret opzionali non configurati diventano file regolari vuoti, così il
contratto `*_FILE` rimane valido anche con root filesystem del container
read-only. `config-set` rifiuta i segreti in command line: usa
`configure --section mail|backup`. Il PAT GHCR rimane soltanto nel credential
store Docker di root.

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
immutabile, crea un database schema-only usando lo schema incluso nell'immagine
di produzione verificata, lo verifica e avvia la webapp. Lo
stato persistente non contiene mai `latest`.
`config-check` non modifica alcun file. SMTP e backup remoto mancanti sono
opzionali; dominio/admin/DNS e accesso all'immagine sono bloccanti. `doctor` verifica invece
l'host e, dopo `init`, anche DB, release e healthcheck.
Apri quindi la dashboard e importa lo ZIP prodotto dagli scraper. Sono accettati
solo package moderni esplicitamente versionati (`mifp-content` v1 oppure
`mifp-jsonl-v2` v2); vecchi ZIP e vecchi JSONL self-contained sono rifiutati.


## `events.mifp.eu`: transizione del publisher

**Phase 1 (corrente):** `EVENTS_PUBLISH_BACKEND=local-vps`. Il DNS di
`events.mifp.eu` punta alla VPS; Caddy gestisce HTTPS e serve
`/srv/mifp-events`. WEBSITE ZIP validati vengono pubblicati attraverso
`EventSitePublisher`. Tutto il PHP è negato, salvo un path esatto
`<public_path>/regform` approvato dall'operatore; quel solo `.php` viene inviato
al pool PHP-FPM host dedicato. Dati di registrazione e upload persistenti stanno
in `/srv/mifp-events-private`, mai nel document root.

```bash
sudo mifpctl events-php-list
sudo mifpctl events-php-enable PLMCN-2027/regform
sudo mifpctl events-php-disable PLMCN-2027/regform
```

**Phase 2 (futura, solo dopo la riparazione Aruba):** backend remoto/FTPS e DNS
di `events.mifp.eu` verso l'hosting esterno. In quella fase la VPS non crea il
virtual host eventi e non usa PHP-FPM per i micrositi. Il DB resta neutrale al
backend e `public_path` conserva l'identità di pubblicazione. Prima del cutover
devono essere definiti e verificati backup, retention e restore delle
registrazioni/upload autorevoli sul provider remoto: il backup VPS non li
include e non li protegge.

## Tre cicli separati

### Aggiornare gli strumenti host (senza aggiornare l'app)

Una modifica sotto `deploy/` non è una nuova immagine applicativa. Da un
checkout verificato, copia manualmente l'intero bundle e avvia lo script che si
trova **nel bundle copiato**:

```bash
# workstation
rsync -a --delete deploy/ OPERATOR@VPS:/tmp/mifp-deploy/

# VPS
sudo bash /tmp/mifp-deploy/refresh-host-tools.sh
sudo mifpctl config-check
sudo mifpctl security-check
```

`refresh-host-tools.sh` è riservato a un host già inizializzato. Verifica che il
bundle sia completo, rifiuta symlink/file modificabili da gruppo o altri,
controlla la sintassi shell/Python e valida Compose prima di installare i file
con sostituzioni atomiche. Rifiuta inoltre il vecchio schema di Docker secrets
`environment:` incompatibile con `read_only: true` e rigenera i file derivati
root-only in `/etc/mifp/secrets/` senza modificare `secrets.env`. Aggiorna il
wrapper, `deploy.sh`, helper, backup,
template, Compose e unit systemd; esegue `daemon-reload` soltanto se le unit sono
cambiate. Non esegue apt, non modifica firewall/SSH/repository Docker, non
tocca il Caddyfile live, non riavvia container o servizi e conserva
`/etc/mifp/{config,secrets}.env`, `/opt/mifp/.env`, release/upgrade state, DB,
dati e backup.
Se cambia il template Caddy, il comando lascia intenzionalmente invariata la
configurazione live e segnala di revisionarla e applicarla esplicitamente con
`sudo mifpctl configure --section web` (che può riavviare l'app in esecuzione).
Una nuova Compose entra invece in vigore solo al successivo deploy/restart
applicativo esplicito.

Questo sostituisce il vecchio uso del bootstrap idempotente per il solo refresh
degli strumenti. `bootstrap-vps.sh` resta il percorso corretto per una prima
installazione o per modifiche esplicite di provisioning host. Non esiste alcun
self-update remoto: la sorgente è sempre il bundle `deploy/` copiato a mano.

Se il refresh include una nuova definizione Compose, questa viene usata solo al
successivo comando applicativo esplicito. Per aggiornare poi l'applicazione:

```bash
sudo mifpctl update-check
sudo mifpctl update
sudo mifpctl status
sudo mifpctl doctor
```

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

Procedura completa di aggiornamento sicuro, senza checkout del sorgente sulla
VPS:

```bash
# VPS: la pipeline crea e verifica una snapshot prima dello switch
sudo mifpctl update-check
sudo mifpctl update
sudo mifpctl status
sudo mifpctl doctor
sudo mifpctl security-check

# workstation, dal checkout corrispondente alla release
./mifp security production https://www.mifp.eu --strict
```

Se health/readiness o la verifica esterna falliscono, conserva i log con
`sudo mifpctl logs` e usa `sudo mifpctl rollback`. Il normale deploy non migra
il database; quando serve una migrazione, usare esclusivamente il flusso
`upgrade-db` descritto sotto, che prova immagine e DB candidato su una copia e
mantiene il rollback congiunto.

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
  dominio e `www`, così i check eseguiti dalla VM si autorisolvono;
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

I DB storici fuori dalla catena di migrazione supportata non vengono "riparati"
automaticamente: crea un DB corrente e rigenera/converti i contenuti in un package moderno
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
sono snapshot point-in-time complete v5 sotto `/var/backups/mifp/snapshots/`.
In Phase 1 (`local-vps`) includono:

```text
snapshot-YYYYMMDD-HHMMSS-NNNNNNNNN/
  mifp.db
  mifp.db.sha256
  manifest.json
  assets/
  conferences/
  config/
  events-private/
  events-php-enabled.txt
  README.txt
```

Il container viene brevemente messo in pausa durante la fotografia dei file;
SQLite viene copiato con la Backup API e verificato. `manifest.json` registra
l'SHA-256 dell'intero insieme ripristinabile (`mifp.db`, `assets/`, `conferences/`,
`config/`, registrazioni/upload privati ed allow-list PHP) e il restore rifiuta
file mancanti, extra, alterati o symlink. Le
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
sudo mifpctl events-republish-all
```

Entrambi pre-validano il DB e non effettuano alcuno swap se il servizio non
si arresta correttamente. Il restore completo verifica prima anche il manifest
di integrità; se la webapp non torna ready, viene ripristinata la fotografia
precedente. Per disaster recovery conserva anche una copia **off-site**;
se configuri restic, ogni snapshot completata viene replicata cifrata.
Il tree pubblico estratto `/srv/mifp-events` non viene duplicato: il secondo
comando lo ricostruisce deterministicamente dai WEBSITE ZIP conservati e poi
riattiva soltanto gli allow-list regform i cui path sono tornati validi. I dati
privati di registrazione non vengono rigenerati: sono ripristinati dalla snapshot.
Con backend remoto la snapshot non richiede né include runtime PHP, dati privati
o allow-list locali e non protegge le submission remote; la relativa strategia
di backup/restore deve essere verificata sul provider prima del cutover.

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

La sezione `mail` è l'unico punto operativo per le credenziali SMTP. La password
viene letta con input nascosto e salvata esclusivamente come valore canonico in
`/etc/mifp/secrets.env` (`0600`). `mifpctl` ne genera una copia derivata
root-only in `/etc/mifp/secrets/mifp_smtp_password`; Compose usa esclusivamente
la sorgente file-backed e la consegna al solo servizio web come
`/run/secrets/mifp_smtp_password`. Non viene inserita nell'immagine,
nel file runtime `.env` né nell'environment del container. La dashboard non rende
host, username o password SMTP. In `local-vps`, la stessa configurazione alimenta
anche il relay
`sendmail` compatibile usato dai `regform` PHP tramite un `/etc/msmtprc`
root-owned e leggibile soltanto dall'utente PHP dedicato. Gli ZIP evento e
`regform/settings.yaml` non devono contenere password SMTP. Cambiando in futuro
le credenziali (per esempio verso SMTP Aruba) basta rieseguire
`sudo mifpctl configure --section mail`; la configurazione host viene rigenerata
senza esporre il segreto nella command line o nei log.

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
[ ] policy SSH effettiva revisionata con sshd -T; se password/root password sono
    attivi, i WARN di security-check sono compresi e vengono usate password robuste
[ ] opzionale: ssh-harden applicato e provato da una nuova sessione se si decide
    in futuro di passare all'autenticazione key-only
[ ] unattended-upgrades attivo e Automatic-Reboot false;
    /var/run/reboot-required assente
[ ] Caddyfile valido e backend Flask solo su 127.0.0.1:8000
[ ] container non-root, rootfs read-only, no-new-privileges, niente docker.sock
[ ] /etc/mifp/secrets.env è 0600; /etc/mifp/secrets è 0700 e i file derivati sono 0400 con owner UID/GID runtime
[ ] Docker config root è 0600 se presente
[ ] /etc/mifp è custodito fuori dalla VPS (escrow dei segreti)
[ ] Caddy gestisce soltanto mifp.eu e www.mifp.eu; nessun vhost/ACME per events
[ ] backup locale riuscito e restore provato almeno una volta
[ ] backup off-site verificato, se configurato
```

La checklist operativa completa, con i comandi di verifica, è in
[docs/DEPLOY_NEW_VPS.md](docs/DEPLOY_NEW_VPS.md).

`security-check` è diagnostico e non modifica la macchina. Verifica la policy SSH
**effettiva** (`sshd -T`): password e root-password consentite producono `WARN`
espliciti ma non un errore, perché sono la policy scelta e non vengono descritte
come key-only. Gli errori reali restano bloccanti. Verifica inoltre che UFW sia
attivo con le regole IPv4 e IPv6 attese, i
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

## Version and release visibility

`MIFPAPP/CORE/VERSION` is the source-controlled application version. Production
Compose injects the immutable `MIFP_IMAGE` reference into the container as
`MIFP_RELEASE_REF` and the configured `:latest` discovery channel as
`MIFP_RELEASE_CHANNEL`. The dashboard exposes these values under **System →
Version & Release** without receiving Docker or host-control privileges.

Changing the application version means changing `VERSION` in source control and
shipping a new image. Runtime deployment remains an explicit host operation via
`mifpctl update`, `deploy`, or `rollback`.


## Simple update workflow

For normal operations use only:

```bash
sudo mifpctl check
sudo mifpctl update
sudo mifpctl status
```

`mifpctl check` is the full update preflight: it may pull/cache the candidate
image, validates the host/image deploy contract, database compatibility, Compose
and secret wiring, but never replaces the running release or changes release
state. Output is always verbose enough to show every phase and ends with an
explicit `Production state: UNCHANGED`.

`mifpctl update` repeats the safety checks, starts the pinned candidate digest,
waits visibly for readiness and records the new release only after health succeeds.
Every failure reports its class and one of `UNCHANGED`, `ROLLED BACK SUCCESSFULLY`
or `MANUAL ATTENTION REQUIRED`. There is no separate quiet/default mode. Known
host/Compose creation failures (for example secret, mount, permission or port
configuration errors) are classified before an image rollback is attempted;
those failures reuse the same host configuration and therefore require host
repair rather than a pointless image switch.

`mifpctl update-check` remains available as a lightweight compatibility command
for registry-only discovery and does not pull the candidate.

Images and host tools use an explicit deploy contract. Current host tools support
legacy contract v1 and current contract v2. A candidate requiring a newer contract
is rejected before Compose changes the running application; refresh `deploy/` on
the VPS first, then rerun `mifpctl check`.
