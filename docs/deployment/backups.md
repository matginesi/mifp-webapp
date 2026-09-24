# Backup e disaster recovery

Le snapshot host sono pubblicate sotto
`/var/backups/mifp/snapshots/snapshot-...` e usano il formato
`mifp-host-snapshot` v5. Il manifest registra anche il backend eventi attivo.
Con `local-vps` contengono:

```text
mifp.db
mifp.db.sha256
manifest.json
assets/
conferences/                 # include i WEBSITE ZIP sorgente immutabili
config/
events-private/
  registrations/            # dati autorevoli dei regform
  uploads/                   # upload persistenti dei regform
events-php-enabled.txt       # allow-list <public_path>/regform
README.txt
```

`/srv/mifp-events` non è incluso: contiene copie pubblicate ricostruibili. Le
sessioni PHP e `tmp` sono transitori e vengono ricreati vuoti. Registrazioni,
upload persistenti e allow-list sono autorevoli per Phase 1 e fanno parte della
snapshot soltanto con backend `local-vps`.

Con backend `remote` o `disabled`, backup e restore non richiedono il runtime
PHP locale e non includono `events-private/` né l'allow-list. Questa assenza è
intenzionale e **non** significa che le submission remote siano protette: prima
di abilitare Phase 2 occorre definire sul provider remoto backup, retention e
una prova di restore delle registrazioni e degli upload autorevoli.

Il timer `mifp-backup.timer` esegue `backup.sh` quando
`BACKUP_ENABLED=true`. Con il default `MIFP_BACKUP_QUIESCE=1`, il container e
il pool PHP-FPM eventi vengono fermati/pausati per la breve finestra di copia.
SQLite usa la Backup API; `manifest.json` fissa insieme esatto e SHA-256 di
ogni file. Symlink e file speciali causano il fallimento della snapshot.

Backup manuale:

```bash
sudo mifpctl backup
```

## Ripristino completo su VPS nuova

1. Esegui il bootstrap e configura lo stesso backend di produzione.
2. Ripristina la snapshot:

   ```bash
   sudo mifpctl restore-snapshot /var/backups/mifp/snapshots/snapshot-...
   ```

3. Ricostruisci tutti i siti dai WEBSITE ZIP conservati:

   ```bash
   sudo mifpctl events-republish-all
   ```

Il comando usa `conference_sites.public_path`, checksum/pacchetto registrati nel
DB e il normale `EventSitePublisher`. Ogni evento è isolato: continua dopo un
errore, stampa il riepilogo e restituisce exit non-zero se almeno uno fallisce.
Non elimina i ZIP sorgente e non tocca path non presenti nei metadata.
Recupera i record pubblicati `source_format=legacy-static`, l'unico formato che
il workflow WEBSITE corrente può portare a `published`; gli import
`conference-editor` restano `staged` e `internal` resta `unpublished`.

Durante il restore di una snapshot locale v4/v5, il routing PHP viene sospeso. Solo dopo che
`events-republish-all` ha ricreato i path pubblici, l'allow-list ripristinata
viene rigenerata, validata da Caddy e riattivata. Le registrazioni e gli upload
privati vengono ripristinati direttamente e non dipendono dalla ripubblicazione.

Il solo DB può essere ripristinato con:

```bash
sudo mifpctl restore-db /var/backups/mifp/snapshots/snapshot-.../mifp.db
```

Le snapshot legacy v1-v4 restano leggibili. Le v2 vengono verificate anche nei
vecchi tree evento/PHP, ma quei vecchi document root non vengono ripristinati.

## Copia off-site

Un backup sulla stessa VPS non protegge dalla perdita dell'host. Configurando
`MIFP_RESTIC_REPOSITORY` e la password restic, ogni snapshot completata viene
replicata cifrata. Credenziali e `/etc/mifp` devono essere conservati fuori da
Git, preferibilmente in un password manager o escrow operativo.
