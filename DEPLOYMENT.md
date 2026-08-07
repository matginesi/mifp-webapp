# Deployment MIFP

## Locale

```bash
./mifp local
```

Il launcher prepara `MIFPAPP/CORE/.venv`, `MIFPAPP/CORE/.env`, storage e database. Non considera valido il virtualenv solo perché esiste: verifica gli import runtime e reinstalla automaticamente le dipendenze se Flask o altri moduli richiesti mancano.

## Docker locale

```bash
./mifp docker
```

Usa `MIFPAPP/CORE/compose.yaml`, monta il codice in sola lettura e conserva i dati
in `MIFPAPP/DATABASE`.

## Docker production

```bash
./mifp production
```

Usa Gunicorn, filesystem applicativo read-only, utente non-root, healthcheck e volume
Docker persistente. Il container inizializza e migra SQLite prima di avviare Gunicorn.

Per il deploy pubblico:

```bash
./mifp production example.org
```

Caddy gestisce reverse proxy, HTTPS e rinnovo dei certificati.


## Credenziali amministratore

```bash
./mifp admin
./mifp admin --username matteo
./mifp admin --username matteo
```

La password deve contenere almeno 10 caratteri. Il comando salva soltanto l'hash nella
configurazione. Se Docker locale o production sono già attivi, il launcher ricrea
automaticamente il container `web` e attende nuovamente `/ready`, quindi la nuova
password è utilizzabile subito.

## Aggiornamento

```bash
git pull --ff-only
./mifp production
```

Il volume `mifp-production_mifp-data` non viene eliminato dagli aggiornamenti normali.

## Diagnostica

```bash
./mifp doctor
./mifp status
./mifp logs production
```
