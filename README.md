# MIFP Web Platform

Repository unico per sito pubblico, dashboard amministrativa, acquisizione dati
e storage persistente MIFP.

## Architettura di deploy

Un solo percorso di produzione, tutto il resto è locale:

```text
GitHub Actions (test + build)
        |
        v
   MIFPAPP/CORE (context Docker) -> GHCR
        |
        v
   /opt/mifp (VPS Docker + dati)  -- Caddy (HTTPS) -> 127.0.0.1:8000
```

- **Locale** (sviluppo e manutenzione): il launcher `mifp` gestisce Flask,
  Docker locale, scraper, database, test, backup ZIP e credenziali admin.
- **Produzione**: un'immagine runtime minimale viene costruita e pubblicata su
  GHCR da GitHub Actions. La VPS tira l'immagine e la serve con
  `deploy/deploy.sh` (compose + Caddy). Il deploy non avviene via CI: va
  lanciato manualmente sulla VPS. Nessun comando `production` esiste nel
  launcher locale.
- **Dashboard** = unica superficie amministrativa in produzione.

## Avvio locale

```bash
./mifp init       # prepara .env, virtualenv e storage
./mifp local      # Flask locale
./mifp docker-local  # stack Docker locale (alias: ./mifp docker)
```

Configurare l'ambiente copiando `MIFPAPP/CORE/.env.example`; i file `.env`, le
credenziali, i database e gli export non devono essere versionati né inclusi
nelle immagini.

## Struttura e confini

```text
SCRAPERS/              acquisizione e creazione JSONL/ZIP
  OUTPUTS/             soli artefatti finali importabili
MIFPAPP/CORE/          applicazione Flask e contesto Docker
MIFPAPP/DATABASE/      database, asset, log, backup ed export persistenti
TESTS/                 test del repository (suite webapp versionata)
deploy/                artefatti di deploy della VPS (compose, Caddyfile, script)
.github/workflows/     pipeline CI/CD (test + build immagine GHCR, nessun deploy)
```

Il flusso dei dati è intenzionalmente unidirezionale:

```text
sorgenti -> SCRAPERS/OUTPUTS/*.jsonl + MIFP_IMPORT.zip
         -> import esplicito
         -> MIFPAPP/DATABASE/mifp.db + asset
         -> MIFPAPP/CORE
```

Gli scraper non importano Flask, non aprono SQLite e non modificano
`mifp.db`. Il CORE legge i percorsi persistenti dalla configurazione e non
possiede dati generati. L'immagine Docker ha come contesto `MIFPAPP/CORE` e
copia soltanto i file runtime dichiarati nel `Dockerfile`. Il compose locale
(`MIFPAPP/CORE/compose.local.yaml`) monta il codice in sola lettura; il compose
di produzione (`deploy/compose.production.yaml`) non contiene `build:` e usa
l'immagine GHCR con i dati persistenti su `/opt/mifp/data`.

## Dati e scraper

```bash
./mifp scraper --scrapers all --fresh
./mifp database --fresh
```

Il primo comando produce JSONL canonici e un solo ZIP in `SCRAPERS/OUTPUTS/`.
Il secondo è l'operazione separata che costruisce o aggiorna lo storage in
`MIFPAPP/DATABASE/`. Non eseguire script Python ad hoc contro il database.

## Test e controlli

```bash
./mifp test
bash test_all.sh --suite webapp
```

## Documentazione mantenuta

- [Deploy e CI/CD](DEPLOYMENT.md)
- [Tema pubblico e dashboard](MIFPAPP/CORE/docs/THEME_SYSTEMS.md)
- guida import per agenti/LLM: generata e scaricabile dalla pagina **Import / Export** della dashboard

La documentazione specifica vive accanto al sottosistema che descrive. Questo
file è l'unico README del repository: note temporanee, report di correzione e
istruzioni una tantum appartengono alla cronologia Git o a documenti dedicati.
