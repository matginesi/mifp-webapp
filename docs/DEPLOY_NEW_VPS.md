# Deploy MIFP on a brand-new VPS

This is the operator runbook for going from an **empty, freshly rented VPS** to a
running, hardened MIFP installation.

> **Status of the controls referenced here.** Everything in this document has
> been verified as *code and tooling* in the repository: the Caddyfile was
> rendered and exercised, the container image was built and booted, the backup
> and restore logic was tested against temporary fixtures, and the shell has
> passed `bash -n` and `shellcheck -S warning`. **Nothing here has been verified
> on a live production host, because no production VPS exists yet.** Every
> `[ ]` item in the final checklist is a check *you* must perform on the real
> machine.

Companion documents: [DEPLOYMENT.md](../DEPLOYMENT.md) (reference),
[VPS install guide](deployment/vps-installation.md) (DNS/VirtualBox/`.home.arpa`
details), [hardening](deployment/hardening.md), [backups](deployment/backups.md).

---

## 0. What you need before starting

| Item | Notes |
| --- | --- |
| A VPS | Ubuntu LTS (22.04 or 24.04), root or sudo access |
| A domain | e.g. `mifp.eu`; `www.` and `events.` subdomains will be derived |
| DNS control | A/AAAA records for the apex, `www` and `events` |
| Your SSH public key | See step 3 — this is what protects the host |
| A GitHub PAT (classic) | scope **`read:packages`** only, for pulling the image |
| The `deploy/` directory | copied from this repository (see step 4) |

You do **not** need a venv, a build toolchain, a database or any Python on the
VPS. Production never compiles code and never runs scrapers.

---

## 1. Supported OS

Ubuntu LTS only — the bootstrap refuses anything else:

```bash
lsb_release -a                 # expect Ubuntu 22.04 or 24.04
```

Keep the provider's default kernel. The Docker and Caddy repositories are
configured with their signing keys pinned to a fingerprint (see step 5).

---

## 2. Initial access

Log in as the provider's default user (usually `root`) and immediately apply the
current security updates before installing anything:

```bash
apt-get update && apt-get -y upgrade
[ -f /var/run/reboot-required ] && reboot   # reconnect afterwards
```

---

## 3. Create operator SSH key access

**Do this before anything else.** The bootstrap never changes SSH by itself, and
`mifpctl ssh-harden` (step 6) refuses to act until it can see a working public
key for your operator user. Skipping this step is the only realistic way to lock
yourself out.

On your workstation:

```bash
ssh-keygen -t ed25519 -C "mifp-operator"      # if you do not already have one
ssh-copy-id operator@<VPS-IP>                  # or paste the public key manually
ssh operator@<VPS-IP> 'sudo -n true && echo "sudo OK"'
```

Requirements for the operator account:

* a public key in `~/.ssh/authorized_keys` (mode `600`, `~/.ssh` mode `700`);
* passwordless `sudo` (or you will be prompted during bootstrap).

Verify key-only login works **in a second terminal** and keep the first session
open for the rest of the procedure.

---

## 4. Copy the deployment bundle

From your workstation:

```bash
scp -r deploy operator@<VPS-IP>:/tmp/mifp-deploy
ssh operator@<VPS-IP>
```

---

## 5. Run the bootstrap

```bash
sudo bash /tmp/mifp-deploy/bootstrap-vps.sh \
  --image-repository ghcr.io/<owner>/<repo>
```

What it does, in order:

1. takes an exclusive lock, repairs any half-configured `dpkg`;
2. installs base packages plus `unattended-upgrades`, `needrestart`, `fail2ban`;
3. configures Docker and Caddy from their official signed repositories,
   **verifying the apt signing-key fingerprint** before use;
4. installs `/etc/docker/daemon.json` (`live-restore`, bounded log growth);
5. creates the `mifp`/`mifp-events` users, groups and the `/opt/mifp` +
   `/etc/mifp` layout with restrictive modes;
6. writes the hardened, deny-by-default PHP-FPM pool and validates it;
7. installs the deploy tooling, `mifpctl`, and the backup timer;
8. configures **security-only** unattended upgrades with automatic reboot
   **disabled**;
9. configures a fail2ban jail for SSH;
10. renders the Caddyfile to a temporary file, validates it, then publishes it
    atomically (an invalid render can never break a running Caddy);
11. enables the firewall **allowing SSH first**, then 80/443, and asserts the
    SSH rule and IPv6 rules are present before finishing;
12. prints a post-condition summary and exits non-zero if anything is missing.

It is idempotent and safe to re-run. It deliberately **does not** negotiate a TLS
certificate yet if the domain is not configured, and it writes a placeholder
`respond 503` site until you configure the domain.

> **Fingerprint mismatch.** If Caddy or Docker has rotated its signing key since
> this repository was written, bootstrap stops with an explicit fingerprint
> message. Verify the new fingerprint against the vendor's official install page
> **out of band**, then re-run with
> `MIFP_CADDY_KEY_FINGERPRINT=<verified>` or `MIFP_DOCKER_KEY_FINGERPRINT=<verified>`.
> Do not disable the check.

---

## 6. Harden SSH

```bash
sudo mifpctl ssh-harden --operator operator
```

The staged behaviour matters:

1. **refuses to run** unless it finds a real public key for the operator (this is
   the lockout guard);
2. verifies `sshd -t` passes *before* touching anything;
3. ensures `/etc/ssh/sshd_config.d/*.conf` is actually `Include`d;
4. writes `/etc/ssh/sshd_config.d/99-mifp-hardening.conf`
   (`PasswordAuthentication no`, `PermitRootLogin prohibit-password`,
   `KbdInteractiveAuthentication no`, `PermitEmptyPasswords no`,
   `MaxAuthTries 3`, forwarding disabled);
5. runs `sshd -t` again, and **asserts the effective `sshd -T` values** — a
   cloud-init drop-in that wins over ours aborts the change and removes the file;
6. only then `systemctl reload`s (never `restart`, so your session survives).

The port is **not** changed and SSH is **not** restricted to a fixed source IP:
the host stays administrable from changing networks.

Then verify from a **new** terminal, keeping the old session open:

```bash
ssh -o PreferredAuthentications=publickey operator@<VPS-IP>   # must succeed
ssh -o PreferredAuthentications=password operator@<VPS-IP>    # must be refused
```

Rollback if anything looks wrong:

```bash
sudo mifpctl ssh-rollback
```

---

## 7. Verify the firewall

```bash
sudo ufw status verbose
sudo ss -lntup
```

Expected public surface: **SSH, TCP 80, TCP 443 — nothing else.**

* UFW must show `Status: active`, with `(v6)` rules for the same ports
  (IPv6 filtering is explicit, not incidental).
* SQLite, the Docker daemon, PHP-FPM and the app's `/ready` endpoint must never
  appear as public listeners.
* If a provider image ships an unexpected listener, stop and investigate before
  continuing.

---

## 8. Verify Docker

```bash
docker info --format '{{.SecurityOptions}}'
docker info --format 'live-restore={{.LiveRestoreEnabled}} logging={{.LoggingDriver}}'
docker compose version
sudo systemctl is-active docker
cat /etc/docker/daemon.json
groups | grep -w docker || echo "correctly NOT in the docker group"
```

The Docker daemon must not be reachable over TCP, the `docker` group must stay
empty (membership is equivalent to root), and `/var/run/docker.sock` must keep
its package-default mode.

`sudo mifpctl security-check` inspects these too, and distinguishes severity:

* **error** — the daemon is configured with a `tcp://` socket (in
  `/etc/docker/daemon.json`, on the `dockerd` command line, or listening on
  2375/2376). A TCP Docker API is remote root on the host.
* **WARN** — `live-restore` is disabled, or there is no daemon-level log
  rotation. Neither is an exposure: the compose services already cap their own
  logs at 10 MB × 3. To add the daemon defaults without overwriting an existing
  configuration:

  ```bash
  sudo python3 - <<'PY'
  import json, pathlib
  p = pathlib.Path("/etc/docker/daemon.json")
  data = json.loads(p.read_text()) if p.exists() else {}
  data.setdefault("live-restore", True)
  data.setdefault("log-driver", "json-file")
  data.setdefault("log-opts", {"max-size": "10m", "max-file": "3"})
  p.write_text(json.dumps(data, indent=2) + "\n")
  PY
  sudo systemctl restart docker
  ```

  The bootstrap intentionally writes that file only when it does not already
  exist, so an operator's own daemon configuration is never overwritten.

---

## 9. DNS prerequisites

Create these records **before** expecting a certificate:

| Name | Type | Value |
| --- | --- | --- |
| `<domain>` | A (and AAAA if you have IPv6) | VPS IP |
| `www.<domain>` | CNAME to `<domain>` | |
| `events.<domain>` | A/CNAME | same host |

Verify from the VPS that the public resolver agrees:

```bash
getent hosts <domain> www.<domain> events.<domain>
```

If you also publish an `AAAA` record, the host must actually serve on IPv6 (the
firewall already filters it). A dangling `AAAA` record is the most common cause
of failed ACME challenges.

---

## 10. Application configuration

```bash
sudo mifpctl configure            # progressive wizard: web / mail / backup / registry
sudo mifpctl config-show          # never prints secrets
```

Non-secret configuration lives in `/etc/mifp/config.env` (`root:root`, `0640`);
secrets live in `/etc/mifp/secrets.env` (`root:root`, `0600`).
`mifpctl config-set` refuses secrets on the command line — use
`mifpctl configure --section mail|backup`.

---

## 11. Secrets

* `SECRET_KEY` is generated by the wizard (32 bytes of hex). In production the
  app refuses to start without it, or with a default/short value.
* `ADMIN_PASSWORD_HASH` is created interactively:

  ```bash
  sudo mifpctl admin
  ```

* SMTP and restic credentials, if used:

  ```bash
  sudo mifpctl configure --section mail
  sudo mifpctl configure --section backup
  ```

**Escrow `/etc/mifp/` now.** Backups cover application data, not host
configuration. Store `config.env` and `secrets.env` in your password manager or
another offline location — losing `SECRET_KEY` only invalidates sessions, but
losing the admin hash forces a reset. Never put these values in Git, in a ticket,
or in a chat message.

---

## Historical `events.mifp.eu` archive

The `events.<domain>` vhost is served directly by Caddy from `/opt/mifp/events`;
it is not part of the Flask container. After DNS points `events.<domain>` to the
VPS, validate and publish the old public document root with:

```bash
sudo mifpctl events-check /path/to/events-document-root
sudo mifpctl events-import /path/to/events-document-root
curl -I https://events.<domain>/.mifp-events-health
```

Do not point `events-import` at a full hosting-account backup. The preflight blocks
secret/private payloads, special files and symlinks before anything reaches the
public tree. PHP remains deny-by-default after every import and rollback.

---

## 12. GHCR authentication

```bash
sudo mifpctl registry-login
```

Uses a GitHub classic PAT with **only** `read:packages`, read without echo and
passed to Docker over stdin. It is stored in root's Docker credential store
(`/root/.docker/config.json`, mode `0600`) and never in a MIFP file.

```bash
sudo mifpctl registry-check
```

---

## 13. Initial data installation

A brand-new host has no database. Choose one:

**a. Empty site (start clean)**

```bash
sudo mifpctl init-db sha-<commit>     # creates a schema-only database
```

**b. Restore an existing installation** — see step 19 (disaster recovery) and
restore the snapshot *before* the first deploy.

`mifpctl` never creates or migrates the schema as a side effect of starting the
container; database lifecycle is always an explicit command.

---

## 14. Initial deploy

```bash
sudo mifpctl config-check          # must pass before the first start
sudo mifpctl init                  # pulls :latest once, pins the OCI digest
sudo mifpctl doctor
```

`init` is the **only** command that accepts `:latest`, and only as a transient
selector: it immediately records the immutable `@sha256:…` digest. Every later
release uses `sha-<commit>`:

```bash
sudo mifpctl deploy sha-<commit>
```

The deploy validates configuration, disk and the current database, downloads the
tag, resolves it to a digest, tests the new image against a readable copy of the
database **before** switching, waits for `/ready`, and records
`CURRENT_IMAGE`/`PREVIOUS_IMAGE` only after success.

---

## 15. Health verification

```bash
sudo mifpctl status
curl -fsS https://<domain>/health          # public, minimal
curl -fsS -o /dev/null -w '%{http_code}\n' https://<domain>/ready   # must be 404
sudo mifpctl logs
```

* `/health` is public and exposes only `{"status": "ok"}` in production.
* `/ready` is **blocked at Caddy** and exists only for Docker/deploy checks.
* Confirm the certificate is valid and HTTP redirects to HTTPS:

  ```bash
  curl -sSI https://<domain> | grep -i strict-transport
  curl -sS -o /dev/null -w '%{http_code} -> %{redirect_url}\n' http://<domain>/
  ```

Log in to the dashboard, then import content:

```bash
# from the dashboard: Data portability -> Import -> MIFP_IMPORT.zip
```

---

## 16. Backup configuration

```bash
sudo mifpctl backup                        # immediate snapshot
sudo systemctl list-timers mifp-backup.timer
sudo systemctl status mifp-backup.timer
```

Snapshots land in `/var/backups/mifp/snapshots/snapshot-<UTC>/` with a
SHA-256 manifest covering the database, assets, conferences, config, events,
private event state and the PHP execution allow-list. `latest` points at the
newest.

Retention (`BACKUP_LOCAL_RETENTION`, minimum 2) is configurable with
`mifpctl configure --section backup`. **Do not set it to 1**: the pre-restore
safety snapshot would delete the snapshot you are restoring.

The backup fails closed on an inconsistent state — for example if the PHP
execution allow-list names a directory that no longer exists — rather than
publishing a snapshot that can never be restored.

---

## 17. Off-site backup (optional but recommended)

A backup that only exists on the VPS does not protect you from losing the VPS.

```bash
sudo mifpctl configure --section backup     # set RESTIC_REPOSITORY + RESTIC_PASSWORD
```

Then initialise the repository **once** — this repository does not do it for you:

```bash
sudo RESTIC_PASSWORD='<from secrets.env>' restic -r "<repository>" init
sudo RESTIC_PASSWORD='<from secrets.env>' restic -r "<repository>" check
```

Every completed local snapshot is then replicated with
`restic backup --tag mifp --tag production`, followed by a bounded
`restic forget --keep-daily --keep-weekly --keep-monthly --prune`. Retention
values are validated *before* the upload, so a typo cannot publish an unprunable
snapshot. If the upload fails, the local snapshot stays valid and the unit exits
non-zero.

## 18. Security check

```bash
sudo mifpctl config-check      # read-only configuration readiness
sudo mifpctl security-check    # read-only host surface, SSH policy, firewall, container
sudo mifpctl doctor            # configuration + Docker + Caddy + DB + release + backups
```

None of these commands "fixes" anything: they diagnose. `security-check` reports
the **effective** SSH policy (`sshd -T`), whether UFW is active with the expected
IPv4/IPv6 rules, file modes, unexpected public listeners, container isolation and
whether backup credentials leaked into the web container.

---

## 19. Disaster-recovery smoke test

Do this **once, now**, while the host is healthy — an untested backup is not a
backup. It is non-destructive to the live data:

```bash
# 1. Take a fresh snapshot.
sudo mifpctl backup

# 2. Verify it without restoring: doctor runs the same integrity check.
sudo mifpctl doctor | grep Backups

# 3. Restore that snapshot over the live installation (the command takes its own
#    safety snapshot first, and rolls back if the service does not come back).
sudo mifpctl restore-snapshot /var/backups/mifp/snapshots/snapshot-<stamp>

# 4. Confirm the site and the data are healthy again.
sudo mifpctl status && curl -fsS https://<domain>/health
```

Restoring onto a **replacement host** is: run steps 5–8 (fresh bootstrap +
SSH + firewall), restore `/etc/mifp/` from your escrow (step 11), then

```bash
sudo mifpctl restore-snapshot <copied-snapshot-directory>
```

---

## 20. Rollback

```bash
sudo mifpctl rollback                 # back to PREVIOUS_IMAGE (works offline)
sudo mifpctl rollback-upgrade         # restores the previous image+DB pair
```

The registry holds only immutable digests, so rollback works from the local image
cache even if GHCR or the Internet is unavailable. A normal application rollback
does not modify the database. After a schema upgrade you must use
`rollback-upgrade`, which restores the database too.

---

## 21. What must never be exposed publicly

| Must stay private | Why |
| --- | --- |
| Flask / Gunicorn (`127.0.0.1:8000`) | only Caddy may reach it; it is not written to be Internet-facing |
| `/ready` | leaks operational detail; Caddy returns 404 |
| The SQLite database and `/opt/mifp/data` | application state |
| The Docker daemon / `docker.sock` | equivalent to root on the host |
| PHP-FPM socket and `events-private/` | registration data and sessions |
| `/etc/mifp/secrets.env` | `SECRET_KEY`, admin hash, SMTP and restic credentials |
| `/var/backups/mifp` and the restic repository password | they contain everything |

Verify at the end:

```bash
sudo ss -lntup | grep -vE '127\.0\.0\.1|\[::1\]'
```

---

## WHEN THE VPS IS PURCHASED

This is the checklist for the **live host**. Everything above is repository-side
readiness; the items below are the checks that can only be performed on a real
machine. None of them has been verified yet.

```text
[ ] OS/version verified (Ubuntu LTS) and within support
[ ] security updates applied; /var/run/reboot-required clear
[ ] unattended-upgrades active, Automatic-Reboot false
[ ] SSH key login verified from a NEW session
[ ] SSH effective config audited: sshd -T shows PasswordAuthentication no,
    PermitRootLogin prohibit-password, PermitEmptyPasswords no, MaxAuthTries <= 3
[ ] fail2ban jail active for sshd
[ ] firewall verified IPv4 AND IPv6 (ufw status verbose shows (v6) rules)
[ ] only expected public listeners: SSH, 80, 443 (nothing else)
[ ] Docker daemon not exposed; docker group empty; socket mode default
[ ] Caddy configuration valid on the host (caddy validate)
[ ] DNS resolves correctly for apex, www and events
[ ] TLS certificate valid and auto-renewing; HTTP redirects to HTTPS
[ ] Flask reachable only on 127.0.0.1:8000
[ ] /ready returns 404 from the Internet
[ ] PHP-FPM not public; events vhost denies hidden/secret files (spot-check
    /.gitignore, /.ssh/id_rsa, /<conf>/regform/settings.json -> 404)
[ ] container isolation verified (non-root, read-only rootfs,
    no-new-privileges, cap_drop ALL, no docker.sock)
[ ] application health verified (/health 200)
[ ] application readiness verified (/ready 200 from the host only)
[ ] backup timer active and a snapshot exists
[ ] isolated restore test performed (step 19)
[ ] offsite backup verified if configured (restic snapshots + restic check)
[ ] /etc/mifp escrowed outside the VPS
[ ] sudo mifpctl security-check passes
[ ] sudo mifpctl doctor passes
```

Expected outcome of the whole procedure: **ready to serve**, with every control
either verified on the host or explicitly marked as not yet applicable. This
checklist is what the separate live-VPS audit will use.
