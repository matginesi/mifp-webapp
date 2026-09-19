# MIFP Web Platform

[![CI/CD](https://github.com/matginesi/mifp-webapp/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/matginesi/mifp-webapp/actions/workflows/ci-cd.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)

Sito pubblico e dashboard amministrativa del **Mediterranean Institute of
Fundamental Physics (MIFP)**, con pipeline locale per scraping e gestione dati.
Lo stack resta volutamente semplice: **Flask, SQLite, Docker Compose e Caddy**.
La produzione si rilascia esclusivamente via CI/CD.

## Funzionalità

- **Sito pubblico** — home, eventi, archivio storico, news, membri,
  pubblicazioni, aree di ricerca, sponsor e pagine istituzionali.
- **Ricerca globale (Find)** — un'unica pipeline per il sito pubblico
  (`/search`) e per la dashboard (`/dashboard/search`), su eventi e archivio,
  news, membri, pubblicazioni, aree di ricerca, pagine e sponsor; la dashboard
  cerca anche asset e record delle conferenze.
- **Dashboard amministrativa** — gestione contenuti e asset, conferenze,
  import/export portabile, data quality, safety operations, log e incidenti.
- **Archivio storico eventi** — import una tantum di package versionati con
  metadata ricchi, persone e documenti, serviti internamente sotto `/archive/`.
- **Scraper** — pipeline locale/remota che produce artefatti JSONL + un unico
  ZIP importabile.
- **CI/CD** — suite di test non-browser, immagine immutabile su GHCR e deploy
  manuale sulla VPS.

## Modello mentale

```text
CODICE:      GitHub -> GHCR -> VPS -> Docker
CONTENUTI:   scraper -> ZIP -> Dashboard Import -> SQLite
EVENTI:      backup/editor -> /opt/mifp/events -> Caddy -> events.mifp.eu
BACKUP:      SQLite + file persistenti + eventi -> /var/backups/mifp
```

Questi flussi non si mescolano. Un normale deploy non modifica i dati e un
normale import non sostituisce fisicamente il DB.

## Avvio rapido (locale)

Serve **Python 3.12+**. Un solo virtualenv alla root:

```bash
./mifp setup        # crea/aggiorna l'unico .venv locale
./mifp init         # .env + storage + admin guidato + schema DB se manca
./mifp local        # webapp locale con Flask
# oppure, dentro Docker:
./mifp docker-local
```

La webapp locale risponde su `http://127.0.0.1:8000` (porta configurabile con
`--port`). La dashboard è raggiungibile da `/dashboard`.

## Ricerca globale (Find)

Una sola implementazione (`mifp_app/services/search.py`) serve entrambe le
superfici; cambiano solo le regole di visibilità e le destinazioni.

| Ambito | Percorso | Cosa cerca |
| --- | --- | --- |
| Pubblico | `GET /search?q=…` | Eventi + archivio storico, news, membri, pubblicazioni, aree di ricerca, pagine, sponsor |
| Dashboard | `GET /dashboard/search?q=…` | Tutto quanto sopra (anche in bozza) + asset + conferenze e persone |

Note di comportamento:

- la ricerca pubblica mostra **solo** record pubblicati/attivi e non espone
  dati amministrativi (email, bio, note, percorsi);
- un evento con metadata di archivio compare **una sola volta** e rimanda alla
  pagina `/archive/…` corretta;
- i risultati sono deduplicati, ordinati per pertinenza e limitati; query vuote
  o troppo corte non restituiscono nulla;
- è basata su SQLite (nessun servizio esterno, nessun indice FTS da mantenere):
  è quindi corretta subito dopo import, modifiche, cancellazioni e restore.

## Pipeline dati

```bash
./mifp scrape all --fresh
# poi importa SCRAPERS/OUTPUTS/MIFP_IMPORT.zip dalla dashboard
```

Il builder DB resta disponibile per ricostruzioni/analisi locali esplicite:

```bash
./mifp database --fresh
./mifp db-check
```

Non eseguire script Python ad hoc contro il database.

## Database

`schema.sql` è la fonte di verità fisica dello schema corrente; `db/contract.py`
definisce gli invarianti che un DB deve rispettare per essere avviato.

```bash
./mifp db-init [PATH]
./mifp db-check [PATH]
./mifp db-upgrade-copy OLD NEW
```

Non esistono migration implicite all'avvio. Gli upgrade supportati sono
espliciti e si eseguono sempre su una copia (`db-upgrade-copy`); in questa
versione sono supportati gli upgrade adiacenti v9 -> v10 -> v11 -> v12. DB
legacy/non versionati passano dai package versionati `mifp-content` v1 o
`mifp-jsonl-v2` v2; i vecchi ZIP non versionati sono rifiutati.

JSONL è record-only. ZIP è il formato portabile per record/asset e, negli
export completi della dashboard, stato durevole.

Gli eventi storici recuperati si importano una sola volta dalla sezione
dashboard **Archive**. Restano normali record `events`, con metadata ricchi in
un'estensione 1:1 e file nella libreria asset. Le pagine pubbliche sono servite
internamente sotto `/archive/`; i normali export portabili Events/All
conservano metadata e file senza dipendere dallo ZIP storico originale.

Vedi [schema e lifecycle](docs/database-schema.md) e
[gestione Conference](docs/conference-sites.md).

## Test

```bash
./test_all.sh --suite quick
./test_all.sh --suite all
./test_all.sh --suite scraper
./test_all.sh --suite database
```

I test sono autosufficienti: **non richiedono il database di produzione**. Ogni
suite crea il proprio DB temporaneo/in-memory dallo schema versionato, quindi in
CI girano con database assente (il DB dell'istanza non è mai versionato). La CI
usa lo stesso `requirements.lock` dell'immagine ed esegue le suite non-browser
sui push a `main` (o su avvio manuale). Il repository non usa Dependabot per
aprire PR/branch automatici: gli aggiornamenti delle dipendenze sono espliciti e
passano dalla stessa CI.

## Produzione

L'immagine Docker ha come contesto `MIFPAPP/CORE` e non contiene scraper,
builder DB o CLI locali. Sulla VPS non serve un venv.

Prima installazione:

```bash
sudo bash deploy/bootstrap-vps.sh
sudo mifpctl configure
sudo mifpctl admin
sudo mifpctl ssh-harden --operator <utente>
sudo mifpctl config-check
sudo mifpctl init
sudo mifpctl doctor
sudo mifpctl security-check
```

Procedura completa per una VPS nuova (DNS, SSH, firewall, backup, smoke test di
disaster recovery, checklist finale):
[avvio su una VPS nuova](docs/DEPLOY_NEW_VPS.md).

Il bootstrap prepara soltanto l'host e può terminare senza dominio o admin.
La configurazione progressiva vive in `/etc/mifp/config.env` (non segreti,
`0640`) e `/etc/mifp/secrets.env` (`0600`); il vecchio `/opt/mifp/.env` viene
migrato senza perdere i valori esistenti e resta un file runtime compatibile.
`config-show` non stampa segreti e `config-check` è read-only.

Il package `ghcr.io/matginesi/mifp-webapp` è pubblico: `config-check` e `init`
verificano e scaricano l'immagine anonimamente. `registry-login` resta opzionale
per eventuali package privati; legge il PAT GitHub senza echo, lo passa a Docker
via stdin e non lo salva nella configurazione MIFP. `init` usa `latest` soltanto per individuare la prima immagine e registra
immediatamente il digest OCI immutabile. Per la manutenzione ordinaria
`update-check` confronta il digest attivo con il digest remoto di `:latest`
senza fare pull o restart; `update` risolve `:latest` e riusa la stessa pipeline
sicura di `deploy`, persistendo sempre e solo `@sha256:...`.

Uso normale dopo un push su `main` e CI verde:

```bash
sudo mifpctl update-check
sudo mifpctl update
sudo mifpctl status
sudo mifpctl logs
sudo mifpctl rollback
```

Per una release specifica resta disponibile `sudo mifpctl deploy sha-<commit>`.

I micrositi storici di `events.mifp.eu` sono file host-side, non record Flask.
Prima della pubblicazione il backup passa un preflight che blocca symlink, file
speciali e materiale privato/secret-like:

```bash
sudo mifpctl events-check /path/al/backup/document-root
sudo mifpctl events-import /path/al/backup/document-root
```

Il bootstrap prepara anche PHP-FPM per futuri form, ma PHP resta disabilitato
per ogni URL finché non viene abilitato esplicitamente su un prefix con
`mifpctl events-php-enable`.

Dettagli: [panoramica deploy](DEPLOYMENT.md) e
[guida passo-passo per VPS pubblica e VM locale](docs/deployment/vps-installation.md).

## Repository hygiene

Il repository contiene **codice e configurazione pubblica**, non i dati
dell'istanza. Database SQLite, `SCRAPERS/OUTPUTS`, export, backup, upload, log e
le directory runtime sotto `MIFPAPP/DATABASE/` restano fuori da Git. La CI
esegue:

```bash
python3 tools/check_repo_hygiene.py
```

e fallisce se trova file runtime/generati tracciati, dump JSONL/NDJSON, archivi,
secret-like files o singoli file sorgente oltre 5 MiB. `.gitignore` impedisce
nuovi inserimenti; se dati di questo tipo sono già presenti nella **storia**
Git, vanno rimossi separatamente con una riscrittura controllata della history.

## Struttura

```text
SCRAPERS/            acquisizione + package importabili
MIFPAPP/CORE/        runtime Flask / contesto Docker
MIFPAPP/DATABASE/    storage e builder locali
TESTS/               test
deploy/              bootstrap e operatore VPS
docs/                documentazione corrente
```

## Documentazione

- [Schema database e lifecycle](docs/database-schema.md)
- [Gestione Conference](docs/conference-sites.md)
- [Formato di import](docs/import-format.md)
- [Riferimento CLI](docs/cli-reference.md)
- [Avvio su una VPS nuova](docs/DEPLOY_NEW_VPS.md)
- [Panoramica deploy](DEPLOYMENT.md)
- [Installazione VPS](docs/deployment/vps-installation.md)
- [Backup](docs/deployment/backups.md)
- [Hardening](docs/deployment/hardening.md)
- [Audit di sicurezza](docs/audit/MIFP_PRODUCTION_SECURITY_AUDIT.md)
- [Revisione manutenibilità](docs/audit/MIFP_MAINTAINABILITY_REVIEW.md)

Questo è l'unico README del repository; documenti storici/temporanei non fanno
parte del codebase operativo.

### Versioning

The web application version is source-controlled in `MIFPAPP/CORE/VERSION`.
Dashboard administrators can inspect the running application version, immutable
OCI release digest, database schema and supported package formats under **System
→ Version & Release**. Conference WEBSITE imports also expose current/previous
package versions; a retained rollback copy can be restored from **Conference
sites** without giving the application Docker or host privileges.
