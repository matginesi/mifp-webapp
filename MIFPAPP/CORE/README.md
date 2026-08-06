# MIFP Webapp Core

Questa directory contiene la webapp Flask, il runtime Gunicorn e le configurazioni Docker.
L'entry point operativo resta nella root del progetto:

```bash
./mifp local
./mifp docker
./mifp production
```

## File principali

- `app.py`, `wsgi.py`: applicazione Flask.
- `mifp_app/`: codice della webapp.
- `mifp_archive/`: migrazioni, health e archivi portabili.
- `Dockerfile`: immagine comune per Docker locale e production.
- `compose.yaml`: Docker locale.
- `compose.production.yaml`: Gunicorn production.
- `compose.public.yaml`: Caddy e HTTPS.
- `docker-entrypoint.sh`: inizializzazione idempotente dello storage e migrazioni.
- `.env.example`: configurazione condivisa; `.env` è locale e non va committato.

Non avviare i Compose manualmente salvo debugging: il launcher prepara storage,
credenziali, UID/GID, attende `/ready` e mostra i log in caso di errore.
