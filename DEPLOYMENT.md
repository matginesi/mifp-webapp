# Deployment MIFP

Un solo percorso di produzione: **GitHub Actions → GHCR → VPS Docker → Caddy**.

## Architettura

```text
push su main
   ├─ test     bash test_all.sh --suite webapp
   ├─ build    docker buildx (context MIFPAPP/CORE) -> ghcr.io/<owner>/mifp-webapp:sha-<commit> + :latest
   └─ deploy   appleboy/ssh-action -> sudo -u mifp bash /opt/mifp/deploy.sh ghcr.io/...:sha-<commit>

VPS:
   Caddy (host, porte 80/443, TLS automatico)
     └-> 127.0.0.1:8000
           └-> docker compose (mifp-production)
                 web (immagine GHCR, read-only, non-root, /app/data bind da /opt/mifp/data)
                 storage-init (root, prepara/chown /opt/mifp/data)
```

Il launcher locale (`./mifp`) non ha comandi di produzione: il deploy avviene
solo tramite la pipeline CI/CD.

## Sviluppo locale

```bash
./mifp init          # .env, virtualenv, storage
./mifp local         # Flask locale
./mifp docker-local  # Docker locale (alias: ./mifp docker)
./mifp doctor        # verifica file essenziali e configurazioni compose
```

Il virtualenv non è considerato valido solo perché esiste: il launcher verifica
gli import runtime e reinstalla se Flask o altri moduli mancano.

## Prima installazione sulla VPS

Prerequisiti: VPS Ubuntu 24.04/26.04 con IP pubblico, record DNS
`A mifp.eu` e `A www.mifp.eu`, accesso SSH.

```bash
# 1. Copia gli artefatti di deploy sul server
scp -r deploy user@host:/opt/mifp/deploy
ssh user@host "sudo bash /opt/mifp/deploy/bootstrap-vps.sh --domain mifp.eu"
```

`bootstrap-vps.sh` è idempotente: installa Docker Engine, Docker Compose v2,
Caddy, crea l'utente `mifp`, la struttura `/opt/mifp/{data,...}` e copia
`compose.production.yaml`, `Caddyfile`, `deploy.sh` e il template `.env`.

```bash
# 2. Compila i segreti (sul server, file /opt/mifp/.env)
sudo nano /opt/mifp/.env
#   SECRET_KEY=$(openssl rand -hex 32)            -> valore reale
#   ADMIN_PASSWORD_HASH=<hash generato in locale> -> ./mifp hash

# 3. Copia i dati iniziali (dal backup o da una build locale)
sudo rsync -avz ./MIFPAPP/DATABASE/ user@host:/opt/mifp/data/
sudo chown -R mifp:mifp /opt/mifp

# 4. Pubblica la prima immagine e avvia lo stack
#    (in locale) git push origin main   -> la pipeline build+deploy si occupa di tutto

# 5. Avvia Caddy
sudo systemctl start caddy
```

Per la prima immagine senza aspettare la pipeline:

```bash
# in locale, nel repo
docker buildx build --platform linux/amd64 -t ghcr.io/<owner>/mifp-webapp:sha-<commit> MIFPAPP/CORE
# push alla GHCR, poi sul server:
sudo -u mifp bash /opt/mifp/deploy.sh ghcr.io/<owner>/mifp-webapp:sha-<commit>
```

## Segreti GitHub necessari

Nelle **Settings → Secrets and variables → Actions** del repository:

| Secret            | Uso                                  |
|-------------------|--------------------------------------|
| `VPS_HOST`        | IP o hostname della VPS              |
| `VPS_USER`        | utente con sudo (non `mifp`)         |
| `VPS_SSH_KEY`     | chiave privata SSH                   |
| `VPS_KNOWN_HOSTS` | fingerprint host (per evitare MITM)  |
| `VPS_DOMAIN`      | dominio pubblico (es. `mifp.eu`)     |

## Rilascio e rollback

Un push su `main` testa, costruisce, pubblica su GHCR e fa il deploy in
automatico. Lo script sul server ricorda la release precedente:

```bash
sudo -u mifp bash /opt/mifp/deploy.sh status
sudo -u mifp bash /opt/mifp/deploy.sh --rollback
```

I dati (`/opt/mifp/data`) non vengono mai toccati dagli aggiornamenti: vivono
su un bind mount del host e sono indipendenti dall'immagine.

## Credenziali amministratore

Locale (salva solo l'hash in `MIFPAPP/CORE/.env`):

```bash
./mifp admin
./mifp admin --username matteo
```

Produzione (rotazione): genera un hash in locale senza mai persistirlo e copia
il valore in `/opt/mifp/.env` come `ADMIN_PASSWORD_HASH`, poi ricrea il web
service:

```bash
./mifp hash
sudo -u mifp bash /opt/mifp/deploy.sh status   # o nuovo rilascio
```

## Backup e hardening

Vedi [backups](docs/deployment/backups.md) e
[hardening](docs/deployment/hardening.md). In sintesi, i dati da salvare sono
`/opt/mifp/data/mifp.db`, `/opt/mifp/data/assets/` e i valori di
`/opt/mifp/.env`.

## Diagnostica

```bash
./mifp doctor
./mifp status
./mifp logs [local|docker]
ssh user@host "systemctl status caddy"
ssh user@host "sudo -u mifp bash /opt/mifp/deploy.sh status"
```
