# Hardening VPS

Il bootstrap configura il minimo necessario: Docker, Caddy, SQLite, il pool PHP-FPM eventi,
firewall, utenti dati separati e backup timer.

## SSH e firewall

La policy di produzione iniziale conserva il comportamento SSH del provider:
autenticazione a password e login root con password possono restare attivi. Usa
password robuste e uniche. Il bootstrap non modifica `sshd`; rileva soltanto la
porta della sessione corrente se `--ssh-port` non è specificata, configura UFW
con rate limiting e abilita fail2ban.

Questa protezione riduce i tentativi brute-force ma non equivale alla sola
autenticazione a chiave. Quando l'operatore vorrà adottare key-only potrà usare,
come hardening aggiuntivo opzionale:

```bash
sudo mifpctl ssh-harden --operator USER
# rollback, se necessario:
sudo mifpctl ssh-rollback
```

Il comando verifica prima una chiave funzionante e va provato mantenendo aperta
una sessione esistente.

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

## Caddy e dominio eventi

Caddy è installato dal repository ufficiale. `/ready` viene bloccato
pubblicamente; `/health` espone soltanto lo stato minimo in produzione. In
Phase 1 serve anche `events.mifp.eu` direttamente da `/srv/mifp-events`.

Il sito eventi nega dotfile (salvo `.well-known`), stage/rollback del publisher,
repository, `.env`, database, backup, chiavi/certificati, configurazioni private
e dati di registrazione. `conference.yaml` è l'unico YAML pubblico canonico.
PHP-like è 404 per default; soltanto `.php` sotto regform approvati viene inviato
al socket del pool non privilegiato. Il pool applica `open_basedir`, disabilita
`.user.ini`, URL include, display errors e funzioni di processo, e usa storage
privato separato.

In una futura Phase 2 remota, Caddy non crea il sito eventi e il servizio
PHP-FPM eventi VPS viene disabilitato. Le snapshot VPS non proteggono le
submission remote: il provider deve avere un backup/restore verificato prima
del cutover.

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl status caddy
```

Per i soli domini di laboratorio `.home.arpa`, il bootstrap usa una CA Caddy
locale e un blocco gestito in `/etc/hosts` per l'autorisoluzione della VPS.
Questa eccezione non si applica mai ai domini pubblici: `mifp.eu` e gli altri
domini normali usano DNS/ACME e non ricevono mapping loopback. `caddy trust`
agisce soltanto sulla VPS; l'eventuale root CA va importata manualmente sulla
workstation.

## Segreti

`mifpctl` inizializza `SECRET_KEY`; le credenziali admin vengono impostate
esplicitamente con `mifpctl admin`. È salvato soltanto `ADMIN_PASSWORD_HASH`;
minimo 10 caratteri. Rotazione:

```bash
sudo mifpctl admin
```

Per una dashboard accessibile a pochissimi operatori, un secondo controllo
esterno (VPN/Tailscale/Access/IP allowlist) resta un hardening opzionale, non un
requisito architetturale.

## Verifica read-only

Dopo bootstrap/init e dopo modifiche infrastrutturali esegui:

```bash
sudo mifpctl doctor
sudo mifpctl security-check
```

`security-check` non corregge automaticamente nulla. Legge la configurazione SSH
effettiva con `sshd -T`: password/root-password consentite generano `WARN` chiari
e non vengono chiamate key-only; non fanno fallire il comando da sole. Controlla
inoltre permessi dei segreti, file world-writable, listener TCP pubblici
inattesi, link/file speciali nei tree eventi, staging residui e proprietà di
isolamento del container
(non-root, rootfs read-only, no-new-privileges, niente host networking o
Docker socket).
