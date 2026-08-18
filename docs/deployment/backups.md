# Backups And Restore

MIFP production data lives outside Git. Back up these required data groups:

```text
/opt/mifp/data/mifp.db
/opt/mifp/data/assets/
```

Also keep `/opt/mifp/source/MIFPAPP/CORE/.env` in a secure password manager or
encrypted backup.

## Main Database Backup

Use SQLite online backup when possible:

```bash
stamp="$(date +%Y%m%d-%H%M%S)"
sudo -u mifp sqlite3 /opt/mifp/data/mifp.db ".backup '/opt/mifp/backups/mifp-$stamp.db'"
```

Copy it away from the server:

```bash
rsync -avz user@host:/opt/mifp/backups/mifp-YYYYMMDD-HHMMSS.db ./backups/
```

Conference operational tables are inside `mifp.db`; there is no separate
conference runtime database to back up.

## Assets Backup

```bash
rsync -avz user@host:/opt/mifp/data/assets/ ./backups/assets/
```

Use `--delete` only when you intentionally want the destination to become an
exact mirror.

## Retention And Cleanup

Do not delete backups blindly. First inspect size and age:

```bash
du -sh /opt/mifp/backups
find /opt/mifp/backups -maxdepth 1 -type f -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' | sort
```

Recommended small-site retention:

```text
Keep the newest 7 daily backups.
Keep one backup before every schema/import/deploy operation.
Move monthly archives off the server.
Delete old local backups only after verifying an off-server copy.
```

Dry-run files older than 30 days:

```bash
find /opt/mifp/backups -maxdepth 1 -type f -mtime +30 -print
```

After confirming they are copied elsewhere, delete them explicitly:

```bash
find /opt/mifp/backups -maxdepth 1 -type f -mtime +30 -delete
```

For a local development checkout such as `MIFPAPP/DATABASE/backups`, the same rule
applies: inspect first, copy anything valuable away, then remove only confirmed
old backups. The directory is excluded from code packaging and must not be
committed.

## Restore

Stop the app before replacing SQLite files:

```bash
cd /opt/mifp/source
sudo -u mifp bash deploy.sh native stop
sudo install -o mifp -g mifp -m 640 backup-mifp.db /opt/mifp/data/mifp.db
rsync -avz --delete ./backups/assets/ user@host:/opt/mifp/data/assets/
sudo chown -R mifp:mifp /opt/mifp/data/assets
sudo -u mifp bash deploy.sh native start
curl -fsS http://127.0.0.1:8000/ready
```

If restoring assets from the server itself:

```bash
sudo rsync -av --delete /path/to/assets-backup/ /opt/mifp/data/assets/
sudo chown -R mifp:mifp /opt/mifp/data/assets
```

## Full Recovery Procedure

1. Install Ubuntu and harden SSH/firewall.
2. Install Python, Git, SQLite, Nginx, Certbot.
3. Create the `mifp` service user.
4. Clone the repository into `/opt/mifp/source`.
5. Restore `MIFPAPP/CORE/.env` with mode `600`.
6. Restore `mifp.db`.
7. Restore `assets/`.
9. Recreate the Python virtualenv and install requirements.
10. Start the native service with `bash deploy.sh native start`.
11. Restore Nginx config and certificates or rerun Certbot.
12. Check `/ready`, logs, and public pages.

## Backup Verification

Periodically verify backups on a separate machine:

```bash
sqlite3 mifp-backup.db "PRAGMA integrity_check;"
sqlite3 conference-backup.db "PRAGMA integrity_check;"
find assets -type f | wc -l
```
