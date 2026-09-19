# Guida completa al deploy VPS MIFP

Questa guida copre due installazioni distinte:

1. una VPS pubblica con dominio reale, DNS pubblico e certificati ACME;
2. una VPS in macchina virtuale locale con dominio `*.home.arpa` e CA locale.

Il bootstrap prepara l'host. `mifpctl init` installa la prima release. Non
clonare il repository, non compilare immagini e non eseguire scraper sulla VPS.

## Architettura risultante

```text
Internet/LAN -> Caddy host :443 -> 127.0.0.1:8000 -> container MIFP
                           \-> /opt/mifp/events -> file statici
                           \-> PHP-FPM host solo per path esplicitamente abilitati

/opt/mifp/data/mifp.db     SQLite persistente
/var/backups/mifp          snapshot host
GHCR                       immagini applicative
```

| Elemento | VPS pubblica | VM locale |
|---|---|---|
| Hostname Linux | libero, per esempio `mifp-vps` | per esempio `vpsbox` |
| Dominio applicativo | `mifp.eu` | `vpsbox.home.arpa` |
| Risoluzione | DNS pubblico | file `hosts` della workstation |
| TLS Caddy | ACME pubblico | `tls internal` |
| `/etc/hosts` sulla VPS | nessuna entry MIFP | blocco loopback gestito |
| Trust client | CA pubblica già riconosciuta | root CA Caddy da importare |

Il comportamento `.home.arpa` è esclusivamente un livello di compatibilità per
test locale. Un dominio normale non riceve mai entry MIFP in `/etc/hosts` e non
usa mai la CA locale di Caddy.

## 1. Prerequisiti comuni

Servono:

- una macchina Ubuntu Server pulita con accesso SSH e `sudo`;
- porte TCP 80 e 443 raggiungibili dal client;
- il contenuto completo della directory `deploy/`;
- almeno una pipeline GitHub Actions completata con successo;
- accesso in lettura al package `ghcr.io/matginesi/mifp-webapp`.

Per un package GHCR privato prepara:

- username GitHub;
- Personal Access Token classic;
- scope minimo `read:packages`;
- eventuale autorizzazione SSO dell'organizzazione.

La pipeline pubblica ogni immagine con `sha-<commit>`, la verifica e soltanto
dopo il successo sposta `latest` sul digest verificato. `latest` serve solo al
primo `init`; `/opt/mifp/release.env` conserva sempre un digest immutabile.

Dal computer che contiene il repository copia il bundle:

```bash
scp -r deploy USER@HOST:/tmp/mifp-deploy
ssh USER@HOST
```

Controlla che non manchino file:

```bash
ls -la /tmp/mifp-deploy
```

Devono essere presenti almeno `bootstrap-vps.sh`, `deploy.sh`, `mifpctl`,
`configure.py`, `vps_config.py`, `local-hosts.sh`, `Caddyfile`, `compose.production.yaml` e i
file systemd del backup.

## 2. Deploy su VPS pubblica

### 2.1 Esegui il bootstrap dell'host

Il dominio non è richiesto. Sulla VPS:

```bash
sudo bash /tmp/mifp-deploy/bootstrap-vps.sh \
  --image-repository ghcr.io/matginesi/mifp-webapp
```

Il comando installa Docker Engine, Compose, Caddy, SQLite, PHP-FPM e gli
strumenti host; crea utenti/directory, systemd e firewall; installa `mifpctl`;
infine inizializza i file di configurazione senza chiedere credenziali
applicative. Se manca il dominio, Caddy resta su una configurazione HTTP di
attesa. Il bootstrap è idempotente e non elimina DB, dati o release esistenti.

Un vecchio `/opt/mifp/.env` viene migrato preservando dominio, repository,
admin e segreti esistenti. Dopo la migrazione, i file autorevoli sono:

```text
/etc/mifp/config.env    root:root 0640   valori non sensibili
/etc/mifp/secrets.env   root:root 0600   segreti e hash admin
/opt/mifp/.env          root:root 0600   compatibilità runtime, senza segreti
```

### 2.2 Configura progressivamente dominio e DNS

Avvia il wizard o imposta soltanto i dati già disponibili:

```bash
sudo mifpctl configure --section web
sudo mifpctl config-set DOMAIN mifp.eu
sudo mifpctl config-set PUBLIC_IPV4 203.0.113.10
sudo mifpctl config-set DNS_PROVIDER aruba
sudo mifpctl config-set DNS_EXPECTED_IPV4 203.0.113.10
sudo mifpctl admin
```

Impostando `DOMAIN`, i default diventano `www.DOMAIN` ed `events.DOMAIN`; sono
comunque personalizzabili. La modifica rigenera e valida Caddy prima del reload.
Per un dominio pubblico rimuove un eventuale vecchio blocco MIFP da
`/etc/hosts`, non configura `tls internal` e lascia certificati e redirect al
normale percorso DNS/ACME di Caddy.

Crea record DNS verso l'IP pubblico della VPS:

```text
mifp.eu         A       VPS_IPV4
www.mifp.eu     A       VPS_IPV4
events.mifp.eu  A       VPS_IPV4
```

Aggiungi record `AAAA` soltanto se la VPS ha IPv6 funzionante e le porte 80/443
sono raggiungibili anche via IPv6. Un record `AAAA` errato può impedire la
validazione ACME o rendere il sito intermittente.

Verifica dal computer locale:

```bash
dig +short mifp.eu A
dig +short www.mifp.eu A
dig +short events.mifp.eu A
```

Attendi la propagazione, poi esegui il controllo read-only:

```bash
sudo mifpctl config-show
sudo mifpctl config-check
```

Il controllo mostra record correnti e attesi e spiega quali record correggere
nel pannello Aruba; non modifica mai il provider DNS. L'assenza di credenziali
Docker non rende il sistema `NOT READY` se il package GHCR pubblico è leggibile.

### 2.3 Accesso registry e readiness pre-init

```bash
sudo mifpctl config-check
```

`config-check` verifica anche Docker, Caddy e la possibilità di leggere il
manifest `:latest` anonimamente, senza avviare container o modificare
`release.env`. Per isolare questo controllo usa `sudo mifpctl registry-check`.
`registry-login` è opzionale: usalo solo se un package privato restituisce
realmente `unauthorized`/`denied`. Il PAT resta nel credential store Docker e
non viene scritto nella configurazione MIFP.

### 2.4 Inizializza la prima release

```bash
sudo mifpctl init
```

`init`:

1. valida host e configurazione;
2. scarica `ghcr.io/matginesi/mifp-webapp:latest`;
3. risolve il digest OCI reale;
4. crea il DB schema-only;
5. verifica immagine e DB;
6. avvia Compose e attende `/ready`;
7. abilita il timer backup;
8. registra soltanto il digest immutabile.

Se healthcheck o timer falliscono, DB iniziale e stato release vengono rimossi e
`init` può essere ripetuto dopo aver corretto la causa.

### 2.5 Verifica e apri il sito

```bash
sudo mifpctl doctor
sudo mifpctl status
curl -I https://mifp.eu/health
curl -I https://events.mifp.eu/.mifp-events-health
```

Apri:

```text
https://mifp.eu/
https://mifp.eu/login
https://events.mifp.eu/
```

Dalla dashboard importa il package dati prodotto dagli scraper. Non copiare mai
un `mifp.db` locale sopra il database vivo.

### 2.6 Required, optional e modifiche successive

Per arrivare a `READY` servono `ENVIRONMENT`, i tre domini, repository GHCR,
username e hash admin, `SECRET_KEY`, DNS coerente in produzione e login Docker
a GHCR. `HOSTNAME`, IP pubblici/attesi e provider DNS sono utili alla diagnosi,
ma gli IP diventano vincoli DNS soltanto quando impostati.

SMTP e backup remoto sono opzionali. Il backup locale è attivo per default e
può essere disabilitato esplicitamente:

```bash
sudo mifpctl configure --section mail
# SMTP_SECURITY: tls (normalmente 465), starttls (normalmente 587), none

sudo mifpctl configure --section backup
# BACKUP_ENABLED, BACKUP_LOCAL_RETENTION, RESTIC_REPOSITORY/RESTIC_PASSWORD
```

`RESTIC_REPOSITORY` vuoto significa “solo snapshot locale”, non errore. Se è
presente, `backup.sh` usa `RESTIC_PASSWORD` dal file segreti. Nessuna mail viene
inviata dal bootstrap o dal wizard. Per correggere un singolo non-segreto:

```bash
sudo mifpctl config-set TIMEZONE Europe/Rome
sudo mifpctl config-unset PUBLIC_IPV6
sudo mifpctl config-show
```

Non passare password a `config-set`: il comando le rifiuta per evitare shell
history e process list. Dopo cambi a dominio o mail, ripeti `config-check`; se
la release è già attiva, `configure` la riavvia con l'ambiente aggiornato.

## 3. Deploy su VPS in VM locale

Questa procedura usa come esempio:

```text
hostname Linux:       vpsbox
dominio applicativo:  vpsbox.home.arpa
dominio eventi:       events.vpsbox.home.arpa
```

Hostname e dominio applicativo sono due concetti distinti. Il bootstrap usa il
valore passato a `--domain`, non il nome restituito automaticamente dalla VM.

### 3.1 Configura la rete della VM

In VirtualBox usa normalmente una scheda con bridge, così workstation e VM sono
nella stessa LAN. Avvia Ubuntu e individua l'indirizzo:

```bash
hostname -I
ip -4 route get 1.1.1.1
```

Annota l'IPv4 LAN, per esempio `192.168.1.50`. È consigliabile creare una
prenotazione DHCP sul router: se l'IP cambia, va aggiornata la workstation.

Verifica dalla workstation:

```bash
ping 192.168.1.50
ssh USER@192.168.1.50
```

### 3.2 Copia il bundle ed esegui il bootstrap

Dalla workstation:

```bash
scp -r deploy USER@192.168.1.50:/tmp/mifp-deploy
ssh USER@192.168.1.50
```

Nella VM:

```bash
sudo bash /tmp/mifp-deploy/bootstrap-vps.sh \
  --image-repository ghcr.io/matginesi/mifp-webapp
sudo mifpctl config-set DOMAIN vpsbox.home.arpa
sudo mifpctl admin
```

Quando `mifpctl` riceve un dominio `.home.arpa`, riconosce automaticamente
`ENVIRONMENT=local` e aggiunge esclusivamente nella VM:

```text
# BEGIN MIFP LOCAL HOSTS
127.0.0.1 vpsbox.home.arpa www.vpsbox.home.arpa events.vpsbox.home.arpa
# END MIFP LOCAL HOSTS
```

Il blocco viene sostituito, non duplicato, a ogni modifica. Caddy riceve
`tls internal`; `mifpctl` prova a installare la CA nel trust store della VM
e mostra il percorso della root CA. Nulla viene modificato automaticamente
sulla workstation.

### 3.3 Configura la risoluzione sulla workstation

`config-set DOMAIN` stampa una riga simile a:

```text
192.168.1.50 vpsbox vpsbox.home.arpa www.vpsbox.home.arpa events.vpsbox.home.arpa
```

Aggiungila al file `hosts` della workstation.

Linux/macOS:

```bash
sudoedit /etc/hosts
```

Windows, da editor avviato come amministratore:

```text
C:\Windows\System32\drivers\etc\hosts
```

Verifica dalla workstation:

```bash
getent hosts vpsbox.home.arpa
getent hosts events.vpsbox.home.arpa
```

Su macOS o Windows usa in alternativa `ping` o `nslookup` tenendo conto che
`nslookup` può non consultare il file `hosts` su tutti i sistemi.

### 3.4 Copia e installa la CA locale sulla workstation

Nella VM prepara una copia leggibile:

```bash
sudo install -o root -g root -m 0644 \
  /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt \
  /tmp/mifp-caddy-local-root.crt
```

Dalla workstation:

```bash
scp USER@192.168.1.50:/tmp/mifp-caddy-local-root.crt ./mifp-caddy-local-root.crt
```

Fedora/RHEL:

```bash
sudo cp mifp-caddy-local-root.crt /etc/pki/ca-trust/source/anchors/
sudo update-ca-trust
```

Ubuntu/Debian:

```bash
sudo cp mifp-caddy-local-root.crt /usr/local/share/ca-certificates/mifp-caddy-local-root.crt
sudo update-ca-certificates
```

macOS:

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain mifp-caddy-local-root.crt
```

Su Windows importa il certificato in “Trusted Root Certification Authorities”
del computer locale. Alcune configurazioni Firefox usano uno store separato:
in quel caso importa la stessa root CA dalle impostazioni certificati del
browser.

Fidati di questa CA soltanto su macchine di laboratorio controllate. Non
distribuirla né usarla per domini pubblici.

### 3.5 Login, init e verifica della VM

Nella VM:

```bash
sudo mifpctl config-check
sudo mifpctl init
sudo mifpctl doctor
```

In local mode `config-check` salta consapevolmente il DNS pubblico. SMTP,
backup off-site e autenticazione GHCR restano opzionali; admin e accesso
all'immagine no.

Dalla workstation:

```bash
curl -I https://vpsbox.home.arpa/health
curl -I https://events.vpsbox.home.arpa/.mifp-events-health
```

Apri:

```text
https://vpsbox.home.arpa/
https://vpsbox.home.arpa/login
https://events.vpsbox.home.arpa/
```

Non usare `curl -k` come soluzione permanente: nasconde una CA non installata o
un hostname errato.

## 4. Deploy delle versioni successive

Dopo un push su `main`, attendi che GitHub Actions sia verde e usa il canale
`latest` soltanto per scoprire la release verificata più recente:

```bash
sudo mifpctl update-check
sudo mifpctl update
sudo mifpctl status
sudo mifpctl doctor
```

`update-check` non scarica immagini e non riavvia servizi. `update` risolve
`:latest` nel digest OCI immutabile e riusa la normale pipeline di deploy. Se
il digest è già attivo termina con `Already up to date` senza restart.

Per installare esplicitamente un commit pubblicato dalla CI:

```bash
sudo mifpctl deploy sha-COMMIT_GIT_COMPLETO
```

Il deploy non modifica il database. L'immagine viene fissata al digest, testata
contro una snapshot del DB e registrata soltanto dopo il healthcheck.

Rollback applicativo:

```bash
sudo mifpctl rollback
sudo mifpctl doctor
```

Stato e log:

```bash
sudo mifpctl status
sudo mifpctl logs
```

## 5. Backup, eventi e PHP

Backup immediato:

```bash
sudo mifpctl backup
```

Preflight e import del document root eventi:

```bash
sudo mifpctl events-check /percorso/al/document-root
sudo mifpctl events-import /percorso/al/document-root
```

`events-check` è read-only e blocca prima della pubblicazione symlink, file speciali,
filesystem annidati, `.env`, repository VCS, database/dump, chiavi/credential e dati
legacy di registrazione. Usa come sorgente la document root pubblica, non l'intero
backup dell'account hosting.

Import e rollback completi azzerano sempre la allow-list PHP. PHP resta
deny-by-default. Abilita soltanto un path verificato che contiene realmente
file PHP:

```bash
sudo mifpctl events-php-enable PLMCN-2027/regform
sudo mifpctl events-php-list
sudo mifpctl events-php-disable PLMCN-2027/regform
```

## 6. Troubleshooting

### GHCR risponde `denied` o `unauthorized`

```bash
sudo mifpctl registry-login
sudo mifpctl init                  # prima installazione
# oppure
sudo mifpctl update                # aggiornamento normale da :latest verificato
# oppure: sudo mifpctl deploy sha-COMMIT
```

Controlla che il PAT sia classic, abbia `read:packages` e possa accedere al
package/organizzazione.

### `manifest unknown` per `latest`

Controlla che GitHub Actions abbia completato anche il job “Promote verified
image to latest”. `latest` non viene pubblicato se verifica immagine o
healthcheck CI falliscono.

### `HTTPS events: ERROR` su VM

Nella VM:

```bash
getent hosts vpsbox.home.arpa
getent hosts events.vpsbox.home.arpa
sudo systemctl status caddy --no-pager
sudo journalctl -u caddy -n 100 --no-pager
```

Entrambi i domini devono risolvere `127.0.0.1` nella VM. Sulla workstation
devono invece risolvere l'IP LAN della VM.

### Il browser segnala certificato non attendibile sulla VM

Verifica di avere importato la root CA di Caddy sulla workstation, non il
certificato del singolo sito. Chiudi e riapri completamente il browser dopo
l'import.

### Caddy non parte

```bash
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo journalctl -u caddy -n 100 --no-pager
```

Su produzione verifica DNS e raggiungibilità delle porte 80/443. Non aggiungere
`tls internal` per aggirare problemi DNS/ACME.

### `init` dice che la release è già inizializzata

Non ripetere `init`. Usa:

```bash
sudo mifpctl deploy sha-COMMIT_GIT_COMPLETO
```

### Verifica dello stato persistente

```bash
sudo cat /opt/mifp/release.env
```

`CURRENT_IMAGE` e `PREVIOUS_IMAGE`, quando presenti, devono essere reference
`ghcr.io/...@sha256:...`, mai `:latest`.

## 7. Checklist finali

VPS pubblica:

- DNS `A`/`AAAA` corretto per dominio, `www` ed `events`;
- nessuna entry MIFP in `/etc/hosts`;
- nessun `tls internal` in `/etc/caddy/Caddyfile`;
- `sudo mifpctl doctor` termina con `Doctor: OK`;
- HTTPS usa una CA pubblica;
- backup timer attivo.

VM locale:

- rete bridge e IP LAN raggiungibile;
- blocco MIFP loopback presente nella VM una sola volta;
- riga con IP LAN presente nella workstation;
- `tls internal` presente solo nella configurazione della VM;
- root CA Caddy installata sulla workstation;
- `sudo mifpctl doctor` termina con `Doctor: OK`;
- backup timer attivo dopo `init`.
