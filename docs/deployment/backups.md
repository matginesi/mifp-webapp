# Backup e restore

Il backup di sicurezza vive **fuori** da `/opt/mifp/data` e usa snapshot
point-in-time complete:

```text
/var/backups/mifp/snapshots/
  snapshot-YYYYMMDD-HHMMSS-NNNNNNNNN/
    mifp.db
    mifp.db.sha256
    manifest.json
    assets/
    conferences/
    config/
    README.txt
```

`mifp-backup.timer` esegue automaticamente il backup. Per garantire che DB e filesystem appartengano alla stessa fotografia, il container web viene brevemente messo in pausa durante la copia (`MIFP_BACKUP_QUIESCE=1`, default). Manualmente:

```bash
sudo mifpctl backup
```

Il DB viene copiato tramite SQLite `.backup`, quindi verificato con
`quick_check` e `foreign_key_check`. `mifp.db.sha256` resta disponibile per una
verifica manuale rapida; `manifest.json` contiene invece SHA-256 e insieme
esatto di **tutti** i file ripristinabili: DB, `assets/`, `conferences/` e
`config/`. Symlink nei tree gestiti sono rifiutati. `rsync --link-dest` usa
hardlink per i file invariati rispetto alla snapshot precedente, quindi non
serve un mirror cumulativo ambiguo. La retention delle snapshot complete è
`MIFP_BACKUP_KEEP` (default 14).

Verifica manuale:

```bash
cd /var/backups/mifp/snapshots/snapshot-...
sha256sum -c mifp.db.sha256
python3 -m json.tool manifest.json >/dev/null
sqlite3 mifp.db 'PRAGMA quick_check; PRAGMA foreign_key_check;'
```

Restore di un DB che rispetta lo schema corrente:

```bash
sudo mifpctl restore-db /var/backups/mifp/snapshots/snapshot-.../mifp.db
```

Non copiare manualmente un DB sopra quello live. `restore-db` e
`restore-snapshot` falliscono prima dello swap se il servizio non può essere
fermato; il restore completo valida automaticamente `manifest.json` e crea una
nuova snapshot di sicurezza prima di modificare DB o file. Per dati storici con
schema vecchio crea un DB corrente e rigenera/converti i contenuti in un package
moderno `mifp-content` v1 o `mifp-jsonl-v2` v2 prima dell’import.

## Copia off-site opzionale

Un backup sullo stesso server non protegge dalla perdita della VPS. Il bootstrap
installa anche `restic`, ma non invia nulla all'esterno finché non configuri:

```text
MIFP_RESTIC_REPOSITORY=...
MIFP_RESTIC_PASSWORD_FILE=/root/.config/mifp/restic-password
```

La password restic deve vivere fuori dal repository, con permessi root-only.
Ogni snapshot completata viene quindi replicata cifrata da restic.
