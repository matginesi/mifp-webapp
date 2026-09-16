# MIFP Web Platform

Sito pubblico + dashboard amministrativa MIFP, con pipeline locale per scraping
e gestione dati. La produzione resta volutamente semplice: Flask, SQLite,
Docker Compose e Caddy.

## Modello mentale

```text
CODICE:      GitHub -> GHCR -> VPS -> Docker
CONTENUTI:   scraper -> ZIP -> Dashboard Import -> SQLite
EVENTI:      backup/editor -> /opt/mifp/events -> Caddy -> events.mifp.eu
BACKUP:      SQLite + file persistenti + eventi -> /var/backups/mifp
```

Questi flussi non si mescolano. Un normale deploy non modifica i dati e un
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

Non esistono migration implicite all'avvio. Gli upgrade supportati sono espliciti e
si eseguono sempre su una copia (`db-upgrade-copy`); in questa versione sono supportati
gli upgrade adiacenti v9 -> v10 -> v11. DB legacy/non versionati continuano a passare dai package
versionati `mifp-content` v1 o `mifp-jsonl-v2` v2; i vecchi ZIP non versionati sono rifiutati.

JSONL è record-only. ZIP è il formato portabile per record/asset e, negli
export completi della dashboard, stato durevole.

Gli eventi storici recuperati si importano una sola volta dalla sezione
dashboard **Archive**. Restano normali record `events`, con metadata ricchi in
un'estensione 1:1 e file nella libreria asset. Le pagine pubbliche sono servite
internamente sotto `/archive/`; i normali export portabili Events/All
conservano metadata e file senza dipendere dallo ZIP storico originale.

Vedi [schema e lifecycle](docs/database-schema.md) e [gestione Conference](docs/conference-sites.md).

## Produzione

L'immagine Docker ha come contesto `MIFPAPP/CORE` e non contiene scraper,
builder DB o CLI locali. Sulla VPS non serve un venv.

Prima installazione:

```bash
sudo bash deploy/bootstrap-vps.sh
sudo mifpctl configure
sudo mifpctl admin
sudo mifpctl registry-login
sudo mifpctl config-check
sudo mifpctl init
sudo mifpctl doctor
sudo mifpctl security-check
```

Il bootstrap prepara soltanto l'host e può terminare senza dominio o admin.
La configurazione progressiva vive in `/etc/mifp/config.env` (non segreti,
`0640`) e `/etc/mifp/secrets.env` (`0600`); il vecchio `/opt/mifp/.env` viene
migrato senza perdere i valori esistenti e resta un file runtime compatibile.
`config-show` non stampa segreti e `config-check` è read-only.

`registry-login` legge il PAT GitHub senza echo e lo passa a Docker via stdin;
serve un PAT classic con il solo scope `read:packages`. `init` usa `latest`
soltanto per individuare la prima immagine e registra immediatamente il digest
OCI immutabile. I deploy successivi continuano a usare `sha-<commit>`.

Uso normale:

```bash
sudo mifpctl deploy sha-<commit>
sudo mifpctl status
sudo mifpctl logs
sudo mifpctl rollback
```

I micrositi storici di `events.mifp.eu` sono file host-side, non record Flask:

```bash
sudo mifpctl events-import /path/al/backup/document-root
```

Il bootstrap prepara anche PHP-FPM per futuri form, ma PHP resta disabilitato
per ogni URL finché non viene abilitato esplicitamente su un prefix con
`mifpctl events-php-enable`.

Dettagli: [panoramica deploy](DEPLOYMENT.md) e
[guida passo-passo per VPS pubblica e VM locale](docs/deployment/vps-installation.md).

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
