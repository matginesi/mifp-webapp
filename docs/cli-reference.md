# MIFP command reference

## Locale

```bash
./mifp setup
./mifp init
./mifp local
./mifp docker-local
./mifp scrape [remote|local|all]
./mifp database [opzioni]
./mifp db-init [PATH]
./mifp db-check [PATH]
./mifp db-upgrade-copy OLD NEW
./mifp test [quick|webapp|scraper|database|browser|all]
./mifp admin
./mifp doctor
```

`./mifp` è solo locale. Produzione non usa virtualenv o sorgenti checkout.

## VPS

Uso normale:

```bash
sudo mifpctl configure [--section web|mail|backup]
sudo mifpctl config-show
sudo mifpctl config-set KEY VALUE
sudo mifpctl config-unset KEY
sudo mifpctl config-check
sudo mifpctl registry-check
sudo mifpctl admin-reset-password [--username NAME]
sudo mifpctl registry-login
sudo mifpctl init
sudo mifpctl deploy sha-<commit>
sudo mifpctl status
sudo mifpctl logs
sudo mifpctl rollback
sudo mifpctl backup
sudo mifpctl doctor
```

Prima installazione:

```bash
sudo mifpctl configure
sudo mifpctl admin
sudo mifpctl config-check
sudo mifpctl init
```

`config-set` accetta soltanto chiavi non segrete. Password SMTP/restic si
inseriscono nel wizard, senza command line né shell history. `config-show`
riporta per i segreti solo `configured`/`not configured`; `config-check` non
scrive file e ritorna 0 quando tutti i required sono pronti, 1 altrimenti.
Verifica inoltre Docker, Caddy e la leggibilità del manifest GHCR `:latest`;
`registry-check` esegue soltanto quest'ultimo controllo senza cambiare release e
prova prima l'accesso anonimo. `registry-login` è opzionale e serve soltanto se
un registry o package privato risponde realmente `unauthorized`/`denied`; il PAT
rimane nel credential store Docker, mai nella configurazione MIFP.

`init` è l'unico comando autorizzato a usare `latest`, solo come selector:
`release.env` conserva sempre il digest OCI risolto. `first-deploy sha-<commit>`
resta disponibile soltanto per compatibilità operativa.

Manutenzione rara:

```bash
sudo mifpctl admin
sudo mifpctl restore-db BACKUP.db
sudo mifpctl restore-snapshot SNAPSHOT_DIR
sudo mifpctl upgrade-db sha-<commit> NEW.db
sudo mifpctl rollback-upgrade
sudo mifpctl fix-permissions
```

Vedi [`../DEPLOYMENT.md`](../DEPLOYMENT.md).
