# Security Hardening

This checklist applies to both VPS and VM deployments.

## SSH

Install your public key for the administrator account:

```bash
ssh-copy-id user@host
```

Edit `/etc/ssh/sshd_config`:

```text
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

Restart SSH:

```bash
sudo service ssh restart
```

Keep one existing SSH session open while testing a new login.

## Sudo

Use a normal administrator account with sudo. Do not run the application as root.
The web service should run as a dedicated `mifp` user.

```bash
sudo usermod -aG sudo user
sudo useradd --system --home /opt/mifp --shell /usr/sbin/nologin mifp
```

## Firewall

Minimal public rules:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

If SSH uses a non-standard port, allow that port before enabling UFW.

## Fail2ban

```bash
sudo apt install -y fail2ban
sudo service fail2ban start
sudo fail2ban-client status
```

The default SSH jail is sufficient for a simple deployment. Add custom jails only
when there is a concrete need.

## Unattended Security Updates

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure unattended-upgrades
sudo unattended-upgrade --dry-run --debug
```

Review `/etc/apt/apt.conf.d/50unattended-upgrades` and keep security updates
enabled.

## File Permissions

Recommended:

```bash
sudo chown -R mifp:mifp /opt/mifp
sudo chmod 750 /opt/mifp /opt/mifp/data /opt/mifp/logs /opt/mifp/backups
sudo chmod 600 /opt/mifp/.env
sudo chmod 640 /opt/mifp/data/mifp.db
```

Secrets, databases, assets, logs, exports, and backups must remain outside Git.
The container runs as a non-root user and mounts `/opt/mifp/data` read-write;
container rebuilds never touch the host bind mount.

## Application Settings

Production `/opt/mifp/.env` must include:

```env
FLASK_ENV='production'
FLASK_DEBUG='0'
SESSION_COOKIE_SECURE='1'
CSRF_ENABLED='1'
TRUST_PROXY='1'
ALLOW_DB_DUMP='0'
```

`TRUST_PROXY=1` is safe only when Caddy is the trusted reverse proxy in front of
the application inside the container.
