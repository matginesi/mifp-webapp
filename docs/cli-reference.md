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
sudo mifpctl deploy sha-<commit>
sudo mifpctl status
sudo mifpctl logs
sudo mifpctl rollback
sudo mifpctl backup
sudo mifpctl doctor
```

Prima installazione:

```bash
sudo mifpctl first-deploy sha-<commit>
```

Manutenzione rara:

```bash
sudo mifpctl admin
sudo mifpctl configure
sudo mifpctl restore-db BACKUP.db
sudo mifpctl restore-snapshot SNAPSHOT_DIR
sudo mifpctl upgrade-db sha-<commit> NEW.db
sudo mifpctl rollback-upgrade
sudo mifpctl fix-permissions
```

Vedi [`../DEPLOYMENT.md`](../DEPLOYMENT.md).
