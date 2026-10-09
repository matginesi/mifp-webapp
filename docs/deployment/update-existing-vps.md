# Aggiornare la VPS MIFP esistente

Per l’integrazione Editor e la correzione persistente del 503 registrazioni,
usare il [runbook specifico](regform-dashboard-update.md), che include anche
l’aggiornamento esplicito del WEBSITE. Questa guida copre l’aggiornamento host.

Procedura per la VPS Ubuntu 24.04 che serve già `mifp.eu` ed
`events.mifp.eu`, con PHP-FPM 8.3 e publisher `local-vps`. Non eseguire
`bootstrap-vps.sh`, `init`, importazioni o ripubblicazioni per questo aggiornamento.
Eseguire un blocco alla volta; fermarsi al primo errore. Non eseguire in parallelo
aggiornamenti, modifiche di configurazione o autorizzazioni PHP.

## 1. Preflight e copia di recupero (VPS)

```bash
sudo mifpctl config-show
sudo mifpctl status
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo php-fpm8.3 -t
sudo systemctl is-active caddy php8.3-fpm
sudo stat -c '%U:%G %a %n' /etc/msmtprc-mifp-events
curl --fail --silent --show-error https://mifp.eu/health
curl --fail --silent --show-error --output /dev/null https://events.mifp.eu/PLMCN-2027/
```

Confermare `EVENTS_PUBLISH_BACKEND=local-vps`, root `/srv/mifp-events` e URL
`https://events.mifp.eu`. Il relay PHP deve essere `mifp-events:mifp-events 600`.
Non stampare il suo contenuto. Se il nome utente dedicato è stato personalizzato,
verificare l'owner effettivo del pool. Non correggere credenziali con questo update.

Creare una copia root-only delle configurazioni e degli strumenti attuali:

```bash
recovery=$(sudo mktemp -d /root/mifp-before-update.XXXXXXXX)
sudo bash -s -- "$recovery" <<'ROOT'
set -euo pipefail
saved=$1
cp -a --parents /etc/mifp /etc/caddy /etc/php/8.3/fpm/pool.d "$saved/"
for file in /etc/msmtprc /etc/msmtprc-mifp-events /usr/local/sbin/mifpctl \
    /etc/systemd/system/mifp-*.service /etc/systemd/system/mifp-*.timer; do
  if [ -f "$file" ]; then cp -a --parents "$file" "$saved/"; fi
done
find /opt/mifp -maxdepth 1 -type f -exec cp -a --parents -t "$saved/" {} +
ROOT
printf 'Recupero configurazioni: %s\n' "$recovery"
sudo mifpctl backup
```

Conservare il percorso stampato nella stessa sessione SSH. La copia contiene
segreti: rimane sotto `/root`, mai nel repository o nel bundle di trasferimento.
Il backup applicativo deve riuscire prima di proseguire; non sostituisce questa
copia delle configurazioni. Nessun passo ripristina o cancella dati applicativi,
siti, registrazioni, allegati o certificati TLS.

## 2. Trasferimento e refresh dei soli strumenti host

Dal TUXEDO usare esattamente i blocchi di
[trasferimento con manifest e staging root-owned](vps-installation.md#host-tools-refresh).
Usare un checkout revisionato della patch: la build Docker arriverà separatamente
tramite CI/GHCR. Nella sessione VPS verificare entrambi gli hash; solo allora:

```bash
sudo bash "$staging/refresh-host-tools.sh"
sudo mifpctl config-check
sudo mifpctl mail-diagnose
```

Il refresh conserva configurazione canonica, SMTP, allowlist PHP e Caddy live;
non aggiorna Docker o PHP-FPM. `mail-diagnose` non invia email: controlla owner,
permessi, corrispondenza con SMTP canonico e configurazione del pool. È normale
che segnali il vecchio `sendmail_path`: confrontarlo con la correzione prevista
qui sotto. **Fermarsi** per differenze SMTP, credenziali mancanti o altri errori;
non eseguire `host-apply mail/all` per tentare di risolverli. Questi comandi
applicano lo stato canonico, anche una disabilitazione esplicita del relay.
Non importare automaticamente credenziali da un relay manuale divergente.

## 3. Applicazione esplicita di PHP-FPM, mail e Caddy

La correzione PHP usa:

```ini
php_admin_value[sendmail_path] = "/usr/bin/msmtp --file=/etc/msmtprc-mifp-events --account=default -t"
```

Il relay resta dedicato, di proprietà dell'utente PHP con permessi `0600`;
nessuna credenziale entra nel container. Quando contenuto e metadati sono già
corretti, il file viene adottato senza sostituzione. Una configurazione manuale
con contenuto equivalente ma formattazione diversa viene rigenerata dai segreti
canonici dopo la verifica dell'operatore.

Rivedere il routing in `deploy/deploy.sh` e il template `deploy/Caddyfile`
prima di applicarlo. Il generatore gestisce il Caddyfile MIFP completo: se il
file live contiene virtual host o personalizzazioni ulteriori, **fermarsi** e
conservarli prima dell'applicazione; questo runbook non autorizza a sovrascriverli.
Applicare in passi separati, così un errore identifica la componente interessata:

```bash
sudo mifpctl host-apply php
sudo mifpctl host-apply mail
sudo mifpctl mail-diagnose
sudo mifpctl host-apply web
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo php-fpm8.3 -t
sudo systemctl is-active caddy php8.3-fpm
sudo cmp "$recovery/opt/mifp/events-php-enabled.txt" /opt/mifp/events-php-enabled.txt
sudo mifpctl doctor
sudo mifpctl security-check
curl --fail --silent --show-error https://mifp.eu/health
curl --fail --silent --show-error --output /dev/null https://events.mifp.eu/PLMCN-2027/regform/
```

L'ultimo URL deve funzionare se PLMCN-2027/regform era già autorizzato; non
inviare il form. Non abilitare PHP per superare un controllo: confrontare la
allowlist originale. Configurazioni invariate evitano reload; il primo inserimento
di Caddy nel gruppo pubblico può richiedere un restart di Caddy. Warning di
reboot vanno valutati separatamente, senza riavvio automatico.

## 4. Aggiornamento Docker, dopo il successo della pipeline

```bash
sudo mifpctl update-check
sudo mifpctl check
sudo mifpctl update
sudo mifpctl status
sudo mifpctl doctor
sudo mifpctl mail-diagnose
```

`update` usa la release GHCR e il workflow esistente; non applica Caddy, non
cambia publisher, non ripubblica siti e non revoca approvazioni PHP. Non usare
`upgrade-db` o `restore-db`: questo aggiornamento non richiede una sostituzione
del database. Verificare la versione **1.0.2** nella dashboard.

## Recupero in caso di errore

Fermarsi prima del passo successivo. La validazione/reload ripristina il file
Caddy precedente e la configurazione pool precedente in caso di errore; i passi
PHP, mail e web non costituiscono una transazione globale.

Per ripristinare il pool o Caddy dalla copia precedente, solo se necessario:

```bash
sudo cp -a "$recovery/etc/php/8.3/fpm/pool.d/mifp-events.conf" /etc/php/8.3/fpm/pool.d/mifp-events.conf
sudo php-fpm8.3 -t && sudo systemctl reload php8.3-fpm
sudo cp -a "$recovery/etc/caddy/mifp-events-php.caddy" /etc/caddy/mifp-events-php.caddy
sudo cp -a "$recovery/etc/caddy/Caddyfile" /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && sudo systemctl reload caddy
```

Per il solo relay PHP, `sudo cp -a "$recovery/etc/msmtprc-mifp-events"
/etc/msmtprc-mifp-events` conserva anche l'owner e `0600` del fix funzionante;
non cambiare i segreti canonici. Per tornare agli strumenti host usare un bundle
precedente verificato con lo stesso trasferimento/refresh; non applicare
nuovamente il suo template PHP/Caddy sopra il fix manuale ripristinato.

Se il problema riguarda la nuova immagine, `sudo mifpctl rollback` torna alla
release precedente senza ripristinare il DB. Poi verificare `status`, `doctor`
e HTTPS. Non usare `restore-snapshot`, reset Docker o ripubblicazioni come
recupero di un problema di configurazione host.

## Serve modificare l'Editor?

**Per questo aggiornamento VPS: no.** Il PHP pubblicato continua a usare
`mail()`; il fix è nel pool e nel relay host. Non modificare SMTP, `mailer.php`
o pacchetti WEBSITE per applicarlo. I micrositi già pubblicati e le loro
approvazioni restano validi.

Le modifiche precedenti dell'Editor per il tracciamento durevole delle notifiche
(`regform/index.php` e `regform/src/bootstrap.php`) sono un aggiornamento distinto
del WEBSITE. Per adottarle servirà esportare il WEBSITE dall'Editor aggiornato,
revocare PHP, sostituire il pacchetto, revisionarlo e riabilitare esplicitamente
`PLMCN-2027/regform`. Non è un prerequisito di questo aggiornamento host.
