# Hardening VPS

Il bootstrap configura il minimo necessario: Docker, Caddy, SQLite, PHP-FPM,
firewall, utenti dati separati e backup timer.

## SSH e firewall

Usa chiavi SSH, disabilita password/root login e mantieni una sessione aperta
mentre modifichi SSH. Il bootstrap rileva la porta della sessione corrente se
`--ssh-port` non è specificata.

Porte pubbliche: SSH, 80 e 443. La webapp è bindata solo su
`127.0.0.1:8000` e non va esposta nel firewall.

## Identità

- host data owner: UID/GID `10001:10001`;
- processo Flask container: stesso UID/GID, non-root;
- utente `mifp` non appartiene al gruppo Docker;
- operazioni Docker host: solo `sudo mifpctl ...`;
- `/opt/mifp/.env`: `root:root`, `0600`.


## Storage runtime

Tutti i tree persistenti usati dall'app (`database`, `assets`, `conferences`,
`config`, `exports`, `logs`, `tmp`) appartengono a `10001:10001`. All'avvio la
webapp rifiuta directory che siano symlink e prova realmente la scrittura;
`/ready` controlla anche la riserva di spazio per tutti questi percorsi. La
snapshot SQLite usata dal preflight del deploy è creata `0440` con ownership del
runtime: il container non-root può leggerla senza rendere il DB scrivibile.

## Container

Produzione usa filesystem root read-only, `cap_drop: ALL`,
`no-new-privileges`, limiti PID/RAM/CPU e un solo worker Gunicorn.

## Caddy ed eventi

Caddy è installato dal repository ufficiale. `/ready` viene bloccato
pubblicamente; `/health` espone soltanto lo stato minimo in produzione.
`events.mifp.eu` viene servito direttamente da `/opt/mifp/events`, che è
root-owned e leggibile soltanto dal gruppo pubblico dedicato. PHP, file di
configurazione del registration form e dati runtime sensibili sono negati per
default.

PHP-FPM gira come utente `mifp-events`, con pool separato, `open_basedir`,
limiti di upload/memoria/tempo e funzioni di shell disabilitate. Il suo storage
scrivibile è `/opt/mifp/events-private`, fuori dal document root. Aggiungere un
path alla allow-list Caddy richiede un comando esplicito
`mifpctl events-php-enable`.

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl status caddy
```

## Segreti

Il bootstrap genera `SECRET_KEY` e chiede admin/password interattivamente. È
salvato soltanto `ADMIN_PASSWORD_HASH`; minimo 10 caratteri. Rotazione:

```bash
sudo mifpctl admin
```

Per una dashboard accessibile a pochissimi operatori, un secondo controllo
esterno (VPN/Tailscale/Access/IP allowlist) resta un hardening opzionale, non un
requisito architetturale.
