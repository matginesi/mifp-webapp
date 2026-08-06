# MIFP Webapp

La root locale può contenere webapp, scraper, database, import data, test e strumenti.
Il launcher operativo è uno solo: `./mifp`.

## Avvio

```bash
./mifp local
./mifp docker
./mifp production
./mifp production example.org
```

- `local`: Flask sul sistema host. Il launcher verifica realmente gli import runtime e crea, ripara o aggiorna il virtualenv quando Flask o altre dipendenze risultano mancanti.
- `docker`: ambiente locale Docker, codice montato e dati in `MIFPAPP/DATABASE`.
- `production`: Gunicorn in container, volume persistente e bootstrap del database.
- `production DOMINIO`: aggiunge Caddy e HTTPS automatico.

Il launcher crea la configurazione locale, genera secret e credenziali admin se mancanti,
attende `/ready` e mostra i log quando un avvio fallisce.

## Pipeline dati

Gli scraper non sono stati rimossi:

```bash
./mifp scrape remote
./mifp scrape local --local-root /percorso/mirror
./mifp scrape all --local-root /percorso/mirror
./mifp database
./mifp refresh remote
```

`SCRAPERS/run_all.sh` resta il motore della pipeline scraper.
`MIFPAPP/DATABASE/build.sh` resta il builder/importer del database.
`IMPORT_DATA/` non viene letta, modificata o cancellata automaticamente.

## Test

```bash
./mifp test
./mifp test webapp
./mifp test scraper
./mifp test database
./mifp test browser
./mifp test all
```

`test_all.sh` rimane il runner interno delle suite complete. Anche la suite browser usa l’output pytest standard con avanzamento percentuale.


## Archivio personale completo

```bash
./zip_it.sh
# oppure
./mifp zip
```

Lo ZIP include webapp, scraper, builder database, test, tools e documentazione anche quando tali cartelle sono escluse dal remoto GitHub. Non include `IMPORT_DATA/`, database SQLite, asset scaricati, output scraper, backup, export, log, virtualenv, cache, secret o archivi precedenti.

## Repository GitHub webapp-only

Le cartelle locali restano nella root, ma `.gitignore` consente al remoto soltanto:

- `MIFPAPP/CORE/`;
- launcher e configurazioni essenziali;
- test webapp;
- workflow CI/CD.

Per sostituire anche la vecchia cronologia remota senza cancellare le cartelle locali, usa la procedura con indice temporaneo documentata in `comandi.txt`. La procedura crea prima un bundle e un branch locale di backup, genera un nuovo commit root rispettando `.gitignore`, aggiorna `main` senza toccare la working tree e usa `--force-with-lease` per evitare di sovrascrivere modifiche remote inattese.

## Comandi principali

```bash
./mifp local
./mifp docker
./mifp production [DOMINIO]
```

Configura username e password amministratore con `./mifp admin`; la password deve avere almeno 10 caratteri. L'hash viene scritto in un formato sicuro per Docker Compose e, se uno stack Docker è già attivo, il servizio web viene ricreato automaticamente per applicare subito le nuove credenziali. Gli scraper accettano `--threads N` e usano come mirror locale predefinito `/run/media/matteo/ARCHDISK/srv/http/mifp.eu`. I comandi di pulizia sono disponibili sotto `./mifp clean`; esegui `./mifp clean all --dry-run` per vedere i target senza modificarli. `IMPORT_DATA/` non viene mai pulita automaticamente.
