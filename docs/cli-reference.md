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
./mifp security status
./mifp security audit [--verbose] [--json] [--strict]
./mifp security config|headers|filesystem|uploads
./mifp security secrets [--git-history] [--json] [--strict]
./mifp security dependencies
./mifp security image IMAGE
./mifp security production https://www.mifp.eu [--json] [--strict]
./mifp security report [--json] [--strict]
```

`./mifp` è solo locale. Produzione non usa virtualenv o sorgenti checkout.

### Diagnostica di sicurezza locale

`security status` e `security audit` verificano configurazione Flask, header,
upload, confini filesystem, gestione dei segreti e contratti Docker/deploy senza
caricare la webapp né leggere i valori dei segreti. `config`, `headers`,
`filesystem` e `uploads` filtrano la stessa raccolta di controlli; `report`
mostra anche le remediation disponibili. Non è un sostituto di
`sudo mifpctl security-check`, che resta il controllo esplicito della VPS.

Opzioni comuni:

- `--json` produce un documento stabile con `key`, `group`, `status`, `summary`
  e `remediation`;
- `--verbose` include le remediation nell'output testuale;
- `--strict` termina con `1` anche in presenza di `WARNING`.

Codici di uscita: `0` nessun finding bloccante, `1` finding di sicurezza
(`CRITICAL`, oppure `WARNING` con `--strict`), `2` input/tool non eseguibile o
target di produzione irraggiungibile. `UNKNOWN` identifica una verifica non
conclusiva e non viene trasformato in un falso finding.

`security secrets` controlla nomi e pattern credential-shaped negli artefatti
tracciati, riportando solo file/riga e mai il valore. Con `--git-history` usa
Gitleaks se già installato; in sua
assenza segnala lo scan come `UNKNOWN` e continua. `security dependencies` usa
`pip-audit` dall'ambiente locale quando disponibile. `security image` usa una
installazione esistente di Trivy. Nessuno dei due scanner viene installato
automaticamente o aggiunto all'immagine di produzione.

`security production` esegue solo richieste HTTP difensive e limitate: verifica
TLS/HTTPS, redirect da HTTP, header, flag dei cookie osservati, `/health` e che
`/ready` sia nascosto. Non effettua crawling, autenticazione, brute force o
modifiche. I test usano risposte simulate e non dipendono da `mifp.eu`.

## VPS

Uso normale:

```bash
sudo mifpctl configure [--section web|mail|backup]
sudo mifpctl config-show
sudo mifpctl config-set KEY VALUE
sudo mifpctl config-unset KEY
sudo mifpctl config-check
sudo mifpctl registry-check
sudo mifpctl admin-reset-password [--username NAME]
sudo mifpctl registry-login
sudo mifpctl init
sudo mifpctl check
sudo mifpctl update
sudo mifpctl update-check
sudo mifpctl version
sudo mifpctl deploy sha-<commit>
sudo mifpctl status
sudo mifpctl logs
sudo mifpctl rollback
sudo mifpctl backup
sudo mifpctl doctor
sudo mifpctl security-check
```

Per gli aggiornamenti ordinari il flusso consigliato è `check -> update -> status`.
`check` esegue il preflight completo (candidate image, deploy contract, DB,
Compose e secrets) senza cambiare release; `update-check` resta il controllo
leggero del solo canale registry. `update` mostra sempre le fasi operative e
termina dichiarando esplicitamente lo stato della produzione.

Prima installazione:

```bash
sudo mifpctl configure
sudo mifpctl admin
sudo mifpctl config-check
sudo mifpctl init
```

`config-set` accetta soltanto chiavi non segrete. Password SMTP/restic si
inseriscono nel wizard, senza command line né shell history. `config-show`
riporta per i segreti solo `configured`/`not configured`; `config-check` non
scrive file e ritorna 0 quando tutti i required sono pronti, 1 altrimenti.
Verifica inoltre Docker, Caddy e la leggibilità del manifest GHCR `:latest`;
`registry-check` esegue soltanto quest'ultimo controllo senza cambiare release e
prova prima l'accesso anonimo. `registry-login` è opzionale e serve soltanto se
un registry o package privato risponde realmente `unauthorized`/`denied`; il PAT
rimane nel credential store Docker, mai nella configurazione MIFP.

`init`, `update-check` e `update` possono consultare `latest` come canale di
discovery, ma `release.env` conserva sempre e soltanto il digest OCI risolto.
`update-check` è read-only; `update` non riavvia nulla quando il digest è già
attivo e, quando cambia, riusa il motore di `deploy`. Il comando esplicito
`deploy` continua a rifiutare tag mutabili. `first-deploy sha-<commit>` resta
disponibile soltanto per compatibilità operativa.

`mifpctl update` aggiorna esclusivamente l'immagine applicativa. Per aggiornare
il wrapper e gli strumenti installati sotto `/opt/mifp`, copia manualmente
l'intero `deploy/` in `/tmp/mifp-deploy/` e usa:

```bash
sudo bash /tmp/mifp-deploy/refresh-host-tools.sh
sudo mifpctl config-check
sudo mifpctl security-check
```

Il refresh non cambia segreti/stato/dati e non riavvia l'applicazione. Il
bootstrap completo resta riservato alla prima installazione o a provisioning
host esplicitamente documentato. Se segnala un template Caddy cambiato,
revisionarlo e applicarlo con `sudo mifpctl configure --section web` (può
riavviare l'app); una Compose aggiornata attende il successivo deploy/restart
esplicito.

Manutenzione rara:

```bash
sudo mifpctl admin
sudo mifpctl restore-db BACKUP.db
sudo mifpctl restore-snapshot SNAPSHOT_DIR
sudo mifpctl upgrade-db sha-<commit> NEW.db
sudo mifpctl rollback-upgrade
sudo mifpctl fix-permissions
```

Vedi [`../DEPLOYMENT.md`](../DEPLOYMENT.md).
