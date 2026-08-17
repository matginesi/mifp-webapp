# MIFP Web Platform

Repository unico per sito pubblico, dashboard amministrativa, acquisizione dati
e storage persistente MIFP. Il launcher `mifp` è l'unico punto di ingresso per
le operazioni ordinarie.

## Avvio

```bash
./mifp local       # Flask locale
./mifp docker      # stack Docker locale
./mifp production  # stack Gunicorn/production
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
TESTS/                 test del repository
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
copia soltanto i file runtime dichiarati nel `Dockerfile`.

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
python3 SCRAPERS/validate_import_data.py SCRAPERS/OUTPUTS
python3 SCRAPERS/validate_artifacts.py SCRAPERS/OUTPUTS
```

## Documentazione mantenuta

- [Tema pubblico e dashboard](MIFPAPP/CORE/docs/THEME_SYSTEMS.md)
- [Deploy](MIFPAPP/CORE/DEPLOYMENT.md)
- [Sicurezza](MIFPAPP/CORE/SECURITY.md)
- guida import per agenti/LLM: generata e scaricabile dalla pagina **Import / Export** della dashboard

La documentazione specifica vive accanto al sottosistema che descrive. Questo
file è l'unico README del repository: note temporanee, report di correzione e
istruzioni una tantum appartengono alla cronologia Git o a documenti dedicati.
