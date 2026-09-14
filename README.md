# MIFP Web Platform

Sito pubblico + dashboard amministrativa MIFP, con pipeline locale per scraping
e gestione dati. La produzione resta volutamente semplice: Flask, SQLite,
Docker Compose e Caddy.

## Modello mentale

```text
CODICE:      GitHub -> GHCR -> VPS -> Docker
CONTENUTI:   scraper -> ZIP -> Dashboard Import -> SQLite
BACKUP:      SQLite + file persistenti -> /var/backups/mifp
```

Questi tre flussi non si mescolano. Un normale deploy non modifica i dati e un
normale import non sostituisce fisicamente il DB.

## Locale

Un solo virtualenv alla root:

```bash
./mifp setup
./mifp init          # .env + admin + DB schema-only se manca
./mifp local
# oppure
./mifp docker-local
```

Pipeline dati:

```bash
./mifp scrape all --fresh
# importa SCRAPERS/OUTPUTS/MIFP_IMPORT.zip dalla dashboard
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

Non esistono migration implicite all'avvio. I vecchi DB non vengono riparati
automaticamente: i dati entrano nel DB corrente solo tramite package versionati
`mifp-content` v1 o `mifp-jsonl-v2` v2; i vecchi ZIP non versionati sono rifiutati.

JSONL è record-only. ZIP è il formato portabile per record/asset e, negli
export completi della dashboard, stato durevole.

Vedi [schema e lifecycle](docs/database-schema.md).

## Produzione

L'immagine Docker ha come contesto `MIFPAPP/CORE` e non contiene scraper,
builder DB o CLI locali. Sulla VPS non serve un venv.

Prima installazione:

```bash
sudo bash deploy/bootstrap-vps.sh --domain mifp.eu --image-repository ghcr.io/OWNER/REPO
sudo mifpctl first-deploy sha-<commit>
```

Uso normale:

```bash
sudo mifpctl deploy sha-<commit>
sudo mifpctl status
sudo mifpctl logs
sudo mifpctl rollback
```

Dettagli: [DEPLOYMENT.md](DEPLOYMENT.md).

## Repository hygiene

Il repository contiene **codice e configurazione pubblica**, non i dati dell'istanza.
Database SQLite, `SCRAPERS/OUTPUTS`, export, backup, upload, log e le directory
runtime sotto `MIFPAPP/DATABASE/` restano fuori da Git. La CI esegue:

```bash
python3 tools/check_repo_hygiene.py
```

e fallisce se trova file runtime/generati tracciati, dump JSONL/NDJSON, archivi,
secret-like files o singoli file sorgente oltre 5 MiB. `.gitignore` impedisce nuovi
inserimenti; se dati di questo tipo sono già presenti nella **storia** Git, vanno
rimossi separatamente con una riscrittura controllata della history.

## Test

```bash
./test_all.sh --suite quick
./test_all.sh --suite all
./test_all.sh --suite scraper
./test_all.sh --suite database
```

I test non richiedono la password admin personale. La CI usa lo stesso
`requirements.lock` dell'immagine, esegue le suite non-browser e costruisce
anche l'immagine Docker sulle pull request senza pubblicarla.

## Struttura

```text
SCRAPERS/            acquisizione + package importabili
MIFPAPP/CORE/        runtime Flask / contesto Docker
MIFPAPP/DATABASE/    storage e builder locali
TESTS/               test
 deploy/             bootstrap e operatore VPS
 docs/               documentazione corrente
```

Questo è l'unico README del repository; documenti storici/temporanei non fanno
parte del codebase operativo.
