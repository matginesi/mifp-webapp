# Guida completa al deploy VPS MIFP

Questa guida copre due installazioni distinte:

1. una VPS pubblica con dominio reale, DNS pubblico e certificati ACME;
2. una VPS in macchina virtuale locale con dominio `*.home.arpa` e CA locale.

Il bootstrap prepara l'host. `mifpctl init` installa la prima release. Non
clonare il repository, non compilare immagini e non eseguire scraper sulla VPS.

## Architettura risultante

```text
Internet/LAN -> Caddy host :443 -> 127.0.0.1:8000 -> container MIFP

/opt/mifp/data/mifp.db     SQLite persistente
/var/backups/mifp          snapshot host
GHCR                       immagini applicative

/srv/mifp-events           siti pubblicati (Phase 1)
/srv/mifp-events-private   registrazioni/upload privati
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

Questa guida descrive la **Phase 1** corrente: publisher `local-vps`, DNS eventi
alla VPS e Caddy/PHP-FPM host. La **Phase 2** sarà possibile solo dopo la
riparazione Aruba: publisher remoto/FTPS, DNS eventi all'hosting esterno e
nessun servizio eventi sulla VPS.

## 1. Prerequisiti comuni

Servono:

- Ubuntu Server 22.04 o 24.04 con accesso SSH e `sudo` (target attuale: 24.04;
  altre release vengono rifiutate);
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

Il primo deploy reale avviene direttamente, senza staging:

```text
repository/CI verde
-> DNS di mifp.eu e www.mifp.eu verso la VPS
-> bootstrap immediato con --domain mifp.eu
-> configurazione applicazione/admin
-> config-check -> init -> doctor -> security-check
-> verifica finale HTTPS, DNS e backup
```

Il cambio DNS prima che l'app sia in esecuzione può causare una breve
indisponibilità, accettata per questo cutover. Inizia con IPv4 (`A` per l'apex,
`A`/`CNAME` per `www`) e aggiungi `AAAA` solo dopo aver verificato
raggiungibilità e firewall IPv6. Nessun dominio di staging fa parte del flusso.

### 2.1 Esegui il bootstrap dell'host

Subito dopo il cambio DNS, sulla VPS:

```bash
sudo bash /tmp/mifp-deploy/bootstrap-vps.sh \
  --domain mifp.eu \
  --image-repository ghcr.io/matginesi/mifp-webapp
```

Il comando installa Docker Engine, Compose, Caddy, SQLite, PHP-FPM e gli
strumenti host; crea utenti/directory, systemd e firewall; installa `mifpctl`;
infine inizializza i file di configurazione senza chiedere credenziali
applicative. Il bootstrap è idempotente e non elimina DB, dati o release
esistenti. Non modifica la policy di autenticazione SSH del provider.

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

Impostando `DOMAIN`, il default pubblico della VPS diventa `www.DOMAIN`. Con
`EVENTS_PUBLISH_BACKEND=local-vps`, `EVENTS_PUBLIC_BASE_URL` è
`https://events.mifp.eu` e Caddy aggiunge il virtual host eventi. Ogni modifica
rigenera e valida Caddy prima del reload.
Per un dominio pubblico rimuove un eventuale vecchio blocco MIFP da
`/etc/hosts`, non configura `tls internal` e lascia certificati e redirect al
normale percorso DNS/ACME di Caddy.

Crea record DNS verso l'IP pubblico della VPS:

```text
mifp.eu         A       VPS_IPV4
www.mifp.eu     CNAME   mifp.eu
events.mifp.eu  A       VPS_IPV4
```

Aggiungi record `AAAA` soltanto dopo aver verificato che la VPS ha IPv6
funzionante, che UFW mostra le regole `(v6)` e che le porte 80/443 sono
raggiungibili via IPv6. Un record `AAAA` errato può impedire la
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

Il controllo mostra i record di apex, `www` e, in modalità `local-vps`, eventi;
in modalità remota salta correttamente DNS/HTTPS VPS per eventi. Non modifica
mai il provider DNS. L'assenza di credenziali
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
sudo mifpctl security-check
sudo mifpctl status
curl -I https://mifp.eu/health
```

Apri:

```text
https://mifp.eu/
https://mifp.eu/login
```

Dalla dashboard importa il package dati prodotto dagli scraper. Non copiare mai
un `mifp.db` locale sopra il database vivo.

In Phase 1 `events.mifp.eu` punta alla VPS ed è incluso nei check DNS/HTTPS e
nel certificato Caddy. I siti sono statici per default. Per un regform PHP già
revisionato usa `sudo mifpctl events-php-enable EVENTO/regform`; upload/import
non abilita mai codice automaticamente.

La policy iniziale conserva password SSH e, se previsto dall'immagine Aruba,
root password login. Usa password robuste: UFW rate limiting e fail2ban
attenuano il brute force ma non equivalgono a key-only. `security-check` mostra
questa scelta come `WARN` senza fallire. `mifpctl ssh-harden --operator USER`
resta un'opzione futura, non un requisito della prima installazione.

### 2.6 Required, optional e modifiche successive

Per arrivare a `READY` servono `ENVIRONMENT`, dominio principale e URL eventi,
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
127.0.0.1 vpsbox.home.arpa www.vpsbox.home.arpa
# END MIFP LOCAL HOSTS
```

Il blocco viene sostituito, non duplicato, a ogni modifica. Caddy riceve
`tls internal`; `mifpctl` prova a installare la CA nel trust store della VM
e mostra il percorso della root CA. Nulla viene modificato automaticamente
sulla workstation.

### 3.3 Configura la risoluzione sulla workstation

`config-set DOMAIN` stampa una riga simile a:

```text
192.168.1.50 vpsbox vpsbox.home.arpa www.vpsbox.home.arpa
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
```

Apri:

```text
https://vpsbox.home.arpa/
https://vpsbox.home.arpa/login
```

Non usare `curl -k` come soluzione permanente: nasconde una CA non installata o
un hostname errato.

## 4. Aggiornamenti successivi

### 4.1 Strumenti host da `deploy/`

Gli strumenti host e l'immagine applicativa hanno cicli distinti. Quando cambia
`deploy/`, copia il bundle completo dalla workstation e usa il refresh ristretto:

```bash
# workstation, dalla root del repository
rsync -a --delete deploy/ OPERATOR@VPS:/tmp/mifp-deploy/

# VPS già inizializzata
sudo bash /tmp/mifp-deploy/refresh-host-tools.sh
sudo mifpctl config-check
sudo mifpctl security-check
```

Il refresh valida completezza e sintassi del bundle, installa atomicamente dove
possibile e ricarica systemd solo se cambiano le unit. Non installa pacchetti,
non modifica UFW o SSH, non riconfigura i repository Docker, non sostituisce il
Caddyfile live e non riavvia l'app. Conserva configurazione, segreti, `.env`,
stato release/upgrade, DB, dati e backup. Anche su host creati col workflow
precedente si esegue direttamente lo script del nuovo bundle copiato; non serve
prima installarlo. Usa ancora `bootstrap-vps.sh` soltanto per il provisioning
iniziale o quando una modifica documentata richiede provisioning host.
Se il template Caddy è cambiato, il refresh non lo applica live: dopo la
revisione usa esplicitamente `sudo mifpctl configure --section web` (può
riavviare l'app). Le modifiche Compose attendono il successivo deploy/restart
applicativo esplicito.

### 4.2 Immagine applicativa

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

## 5. Backup e confine del dominio eventi

Backup immediato:

```bash
sudo mifpctl backup
```

Con `local-vps` le snapshot includono WEBSITE ZIP, registrazioni/upload private
e allow-list PHP, ma non il tree pubblico estratto. Dopo un restore esegui
`sudo mifpctl events-republish-all`: ricostruisce i siti attraverso il publisher
e riattiva l'allow-list solo dopo averne validato i path.

Con backend remoto le snapshot non richiedono né includono il runtime PHP
locale e non proteggono le submission remote. Backup, retention e restore dei
dati autorevoli sul provider remoto devono essere verificati prima di Phase 2.

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

- DNS `A`/`AAAA` corretto per dominio e `www`;
- `events.mifp.eu` risolve alla VPS in Phase 1;
- nessuna entry MIFP in `/etc/hosts`;
- nessun `tls internal` in `/etc/caddy/Caddyfile`;
- `sudo mifpctl doctor` termina con `Doctor: OK`;
- `sudo mifpctl security-check` non riporta errori (i WARN SSH della policy
  password scelta sono stati revisionati);
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
