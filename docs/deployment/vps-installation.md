# Guida completa al deploy VPS MIFP

**La VPS MIFP è già in produzione?** Seguire prima la
[procedura di aggiornamento della VPS esistente](update-existing-vps.md).
Il bootstrap descritto sotto riguarda il provisioning iniziale e non serve
per applicare questa patch all’host attuale.

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

Trasferisci un bundle verificato con la procedura di
[staging sicuro](#host-tools-refresh), valida per installazione e aggiornamenti.
Non eseguire script root dalla directory di ingresso dell'operatore.

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
sudo bash "$staging/bootstrap-vps.sh" \
  --domain mifp.eu \
  --events-backend local-vps \
  --image-repository ghcr.io/matginesi/mifp-webapp
```

Il comando installa Docker Engine, Compose, Caddy, SQLite, PHP-FPM e gli
strumenti host; crea utenti/directory, systemd e firewall; installa `mifpctl`;
infine inizializza i file di configurazione senza chiedere credenziali
applicative. Il bootstrap è idempotente e non elimina DB, dati o release
esistenti. Importa direttamente lo stato canonico o il vecchio runtime: non
precompila un `.env` dai default produzione prima della migrazione. Omettere
`--domain`, `--image-repository` e `--events-backend` su un host già configurato
conserva le scelte esistenti. Non modifica la policy di autenticazione SSH del provider.

Un vecchio `/opt/mifp/.env` viene migrato preservando dominio, repository,
admin e segreti esistenti. Dopo la migrazione, i file autorevoli sono:

```text
/etc/mifp/config.env    root:root 0640   valori non sensibili
/etc/mifp/secrets.env   root:root 0600   fonte canonica segreti e hash admin
/etc/mifp/secrets/      root:root 0700   file Docker derivati (0400, owner UID/GID runtime; anche vuoti se opzionali)
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
`https://events.mifp.eu` e Caddy aggiunge il virtual host eventi. Le modifiche web rigenerano e validano Caddy prima del reload. Una configurazione
invariata non causa reload; le modifiche mail aggiornano soltanto i relay e il monitor.
Il bootstrap e `host-apply all` usano lo stesso provisioning: directory, pool e
socket mancanti vengono creati quando si seleziona `local-vps`. `remote` e
`disabled` preservano i dati e disattivano soltanto il pool dedicato MIFP, senza
fermare PHP-FPM per gli altri siti. La allowlist nuova è vuota; nessun regform
viene autorizzato automaticamente. Anche cambiare `EVENTS_LOCAL_ROOT` richiede
prima la revoca delle autorizzazioni: un altro tree non può ereditarle. Il
controllo si applica anche a `host-apply web` dopo modifiche dirette ai file
canonici.
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
# Aruba: SMTP_HOST=smtps.aruba.it, SMTP_PORT=465, SMTP_SECURITY=tls
# SMTP_FROM_ADDRESS=secretary@mifp.eu; inserire password soltanto nel prompt
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

Il wizard genera atomicamente due file derivati dalle impostazioni canoniche:
`/etc/msmtprc` root:root `0600` per il monitor e i retry host;
`/etc/msmtprc-mifp-events` mifp-events:mifp-events `0600` per PHP.
Il pool usa `/usr/bin/msmtp --file=/etc/msmtprc-mifp-events --account=default -t`.
Owner e modo vengono impostati sul file temporaneo prima della sostituzione;
le rotazioni non richiedono copia o chmod manuali né modifiche al backend Python.
Se SMTP viene disabilitato o reso incompleto, i relay derivati vengono rimossi.
`MAIL_PROVIDER=enabled` mantiene il significato SMTP quando è presente un host;
`disabled` e `console` restano espliciti e non vengono convertiti in SMTP.

Verifica senza inviare email:

```bash
sudo mifpctl mail-diagnose
sudo mifpctl mail-diagnose --network
```

Il primo controlla contenuto canonico, owner/mode, lettura e parser msmtp come
utente PHP e direttiva del pool. Il secondo aggiunge TLS/autenticazione senza
MAIL/RCPT/DATA. Non prova la consegna in casella. Con SMTP disabilitato il
comando lo dichiara esplicitamente; non abilita il servizio.

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

Dalla workstation trasferisci e verifica il bundle seguendo
[lo staging root-owned](#host-tools-refresh), poi collegati alla VM.

Nella VM:

```bash
sudo bash "$staging/bootstrap-vps.sh" \
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

<a id="host-tools-refresh"></a>

### 4.1 Strumenti host da `deploy/`

Gli strumenti host e l'immagine applicativa hanno cicli distinti. Quando cambia
`deploy/`, copia il bundle completo dalla workstation e usa il refresh ristretto:

Il trasferimento usa un ingresso privato dell'operatore e uno staging nuovo,
root-owned sotto `/root`. Servono SSH, sudo e rsync su entrambe le macchine.
Usare un checkout/release verificato: i permessi non attestano la provenienza.
Non eseguire il refresh direttamente dalla directory scrivibile dall'operatore.

Dal TUXEDO, nella root di un checkout/release revisionato, prepara una copia
con permessi normalizzati e un manifest dei file. Il digest del manifest deve
arrivare alla sessione VPS tramite questo output fidato, non letto dall'ingresso:

```bash
bundle=$(mktemp -d)
rsync -rtp --chmod=D700,F600 --exclude='__pycache__/' --exclude='*.pyc' \
  --include='.env.production.example' --exclude='.env*' deploy/ "$bundle/" &&
  (cd "$bundle" && find . -type f ! -path ./BUNDLE.sha256 -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$bundle/BUNDLE.sha256"
sha256sum "$bundle/BUNDLE.sha256"
incoming=$(ssh OPERATOR@VPS 'mktemp -d /tmp/mifp-deploy-incoming.XXXXXXXX') &&
  rsync -rtp --chmod=D700,F600 "$bundle/" "OPERATOR@VPS:$incoming/"
printf 'Ingresso VPS: %s\n' "$incoming"
```

Dopo il successo del trasferimento, nella sessione SSH dell'operatore imposta
`incoming` al percorso stampato e `expected` al digest SHA256 del manifest
stampato **sul TUXEDO**. Non eseguire file provenienti dall'ingresso:

```bash
incoming=/tmp/mifp-deploy-incoming.XXXXXXXX
expected=INSERIRE_DIGEST_SHA256_STAMPATO_SUL_TUXEDO
sudo -v
staging=$(sudo mktemp -d /root/mifp-deploy.XXXXXXXX) &&
  sudo rsync -rtp --chown=root:root --chmod=D700,F600 "$incoming/" "$staging/" &&
  printf '%s  %s\n' "$expected" "$staging/BUNDLE.sha256" | sudo sha256sum --check --strict &&
  sudo bash -c 'cd "$1" && sha256sum --check --strict BUNDLE.sha256' bash "$staging"
```

Prosegui soltanto se **entrambi** i controlli hash terminano con successo. La
copia root-owned è privata e i suoi contenuti sono verificati contro il manifest
fidato prima di eseguire codice root: cambiare owner/mode da solo non rende
fidato il contenuto. Un errore di copia/hash richiede un ingresso e staging nuovi,
non un chmod correttivo. rsync senza `-l`, `-D` o `-a` non conserva symlink,
device o file speciali; gli script rifiutano comunque file non regolari,
file scrivibili da gruppo/altri e staging/file non root-owned.

Su **VPS nuova**, usa la variabile `staging` per il bootstrap del paragrafo 2.1.
Su **VPS esistente**, aggiorna soltanto gli strumenti:

```bash
sudo bash "$staging/refresh-host-tools.sh"
```

Non rieseguire il bootstrap per un update ordinario. Le directory temporanee
contengono solo il bundle e possono essere eliminate dopo averne verificato i
percorsi e concluso le verifiche. Conserva la release precedente verificata per
un eventuale rollback host; non usare il bundle d'ingresso come copia fidata.

Il refresh valida completezza e sintassi del bundle, installa atomicamente dove
possibile, rifiuta secret Compose `environment:` incompatibili con il rootfs
read-only, rigenera `/etc/mifp/secrets/` dalla fonte canonica `secrets.env` e
ricarica systemd solo se cambiano le unit. Non installa pacchetti,
non modifica UFW o SSH, non riconfigura i repository Docker, non sostituisce il
Caddyfile live e non riavvia l'app. Conserva configurazione, segreti, `.env`,
stato release/upgrade, DB, dati e backup. Anche su host creati col workflow
precedente si esegue direttamente lo script del nuovo bundle copiato; non serve
prima installarlo. Usa ancora `bootstrap-vps.sh` soltanto per il provisioning
iniziale o quando una modifica documentata richiede provisioning host.
Il routing eventi è generato da `deploy.sh`, oltre al template `Caddyfile`.
Il refresh segnala modifiche al template e richiede revisione quando cambia
`deploy.sh`; non applica nessuno dei due live: dopo la
revisione usa esplicitamente `sudo mifpctl host-apply web`. Le modifiche Compose attendono il successivo deploy/restart
applicativo esplicito.

Il refresh e i normali `mifpctl update`/`restart` conservano
`EVENTS_PUBLISH_BACKEND=local-vps`, `EVENTS_PUBLIC_BASE_URL` e il Caddyfile live:
`events.mifp.eu` resta servito sulla stessa VPS. Non rieseguire il bootstrap
per un aggiornamento ordinario.

L'applicazione infrastrutturale è distinta dal refresh e dal deploy Docker:

```bash
sudo mifpctl config-show
sudo mifpctl host-apply all   # upgrade di questo bundle: PHP, relay e Caddy
sudo mifpctl config-check
sudo mifpctl doctor
sudo mifpctl security-check
```

`host-apply` legge lo stato canonico salvato e non riavvia il container.
Per aggiornamenti successivi circoscritti usa `host-apply web`, `php` o `mail`.
`web` rigenera template, routing e include PHP sotto lo stesso lock di
pubblicazione, valida e applica con un solo reload. Se file e include sono
invariati, salta il reload. Non ricade su restart dopo un reload fallito e
ripristina i file precedenti. Il primo inserimento di Caddy nel gruppo pubblico
richiede un solo restart intenzionale per acquisire il gruppo; è evitato nelle
esecuzioni successive. `php` valida il pool prima del reload, ripristina il file
precedente in caso di errore e non tronca i log esistenti. La gestione
del gruppo Caddy avviene nel passaggio `web`; per il primo provisioning usa `all`.
I passaggi sono ripetibili dopo un errore; non esiste una transazione unica tra
servizi diversi. Un errore resta esplicito e va risolto prima del deploy app.

La rigenerazione gestisce il Caddyfile MIFP: confronta eventuali personalizzazioni
live prima dell'applicazione. Per rollback host reinstalla il bundle precedente
verificato e riapplica esplicitamente i soli componenti interessati, preservando
configurazione canonica, segreti, dati, backup e allowlist. Non ripristinare il
comando difettoso `--file=/etc/msmtprc` nel pool.

Un aggiornamento Docker non applica nuove regole Caddy/PHP. Gli avvisi reboot
sono separati dagli errori applicativi: pianifica il riavvio host, senza trattare
una sola richiesta reboot come indisponibilità dell'applicazione.

### 4.2 Immagine applicativa

Dopo un push su `main`, attendi che GitHub Actions sia verde e usa il canale
`latest` soltanto per scoprire la release verificata più recente:

```bash
sudo mifpctl check
sudo mifpctl update
sudo mifpctl status
sudo mifpctl doctor
```

`check` è il preflight completo consigliato: può scaricare/cacheare la candidate
ma non cambia la release attiva o lo stato release. `update-check` resta il
controllo leggero del manifest remoto, senza pull o restart. `update` risolve
`:latest` nel digest OCI immutabile e riusa la normale pipeline di deploy. Prima
di ogni operazione Compose materializza i Docker secrets file-backed; durante
l'avvio mostra inoltre l'avanzamento del readiness check invece di restare
silenzioso. Se Compose fallisce prima del readiness, l'errore viene classificato
come problema host/Compose e non viene tentato un rollback immagine destinato a
fallire con la stessa configurazione host. Se il digest è già attivo termina con
`Already up to date` senza restart.

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

## 8. Verifiche locali riproducibili e limiti

La regressione ordinaria si esegue con `bash test_all.sh --suite quick -- -q`.
Il test opt-in del bootstrap richiede un container Ubuntu 24.04 usa e getta:

```bash
# TUXEDO, dalla root; nessun socket Docker o dato host nel container
# Adattare soltanto i due percorsi dei binari Docker se diversi sulla workstation.
docker run --rm \
  --mount type=bind,src="$(pwd)/deploy",dst=/work/deploy,readonly \
  --mount type=bind,src="$(pwd)/TESTS",dst=/work/TESTS,readonly \
  --mount type=bind,src=/usr/bin/docker,dst=/test-bin/docker-real,readonly \
  --mount type=bind,src=/usr/lib/docker/cli-plugins,dst=/usr/lib/docker/cli-plugins,readonly \
  ubuntu:24.04 bash /work/TESTS/tools/bootstrap-ubuntu-runtime.sh
```

Il runner installa i prerequisiti nel container e prova utenti, gruppi, owner/mode,
bootstrap ripetuto, cambio backend, preservazione di impostazioni/dati/backup/log,
refresh e rifiuto dei bundle insicuri. Usa Caddy, PHP-FPM 8.3, msmtp e validazione
Compose reali, HTTPS con CA locale e SMTP catturato su loopback. La catena
HTTPS → pool PHP → `mail()` → msmtp viene eseguita senza inviare email Internet.
`doctor` confronta anche i domini/root della configurazione Caddy caricata via
admin API loopback con lo stato canonico; non basta un file valido sul disco.

Systemd, firewall e daemon Docker sono simulati: il container non dimostra un
boot completo su VM, persistenza dopo reboot, regole firewall effettive, DNS
pubblico, ACME Let's Encrypt, autenticazione Aruba o consegna in casella.
Questi restano controlli finali dell'operatore sulla VPS; il codice non li dichiara
superati in base alla sola presenza dei file. Il test distruttivo rispetto al suo
filesystem rifiuta l'esecuzione fuori da un container root esplicitamente opt-in.
