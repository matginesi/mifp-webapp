# Aggiornamento registrazioni ed Editor sulla VPS esistente

Questa release integra l'Editor nella dashboard. Per eliminare il 503 dopo un
invio non basta aggiornare Docker o eseguire `registrations-access`: occorrono
il runtime PHP aggiornato nel WEBSITE e il flag nel pool. L'aggiornamento non
richiede una VPS nuova, migrazioni del DB o modifiche SMTP/DNS.

Eseguire un passo alla volta, fermandosi al primo errore. Non eseguire altre
pubblicazioni o modifiche delle autorizzazioni durante la procedura.

## Ordine operativo

1. Conservare il WEBSITE autorevole originale e una copia delle configurazioni
   host secondo il [preflight della VPS esistente](update-existing-vps.md#1-preflight-e-copia-di-recupero-vps).
   Eseguire `sudo mifpctl backup` e verificarne il successo. Non copiare dati
   personali o segreti nel repository.
2. Trasferire il bundle `deploy/` revisionato con il
   [manifest e staging root-owned](vps-installation.md#host-tools-refresh), poi:

   ```bash
   sudo bash "$staging/refresh-host-tools.sh"
   sudo mifpctl config-check
   ```

   Il refresh conserva Caddy live, SMTP e autorizzazioni; non applicare
   `host-apply mail/all` per questa patch. Il relay già funzionante resta invariato.
3. Dopo CI verde aggiornare l'applicazione tramite il workflow esistente:

   ```bash
   sudo mifpctl check
   sudo mifpctl update
   sudo mifpctl status
   ```

4. Conservare un export del WEBSITE autorevole di PLMCN-2027. Confrontare e
   aggiornare **nei suoi sorgenti locali** i file standard RegForm dal template
   `MIFPAPP/CORE/CONFERENCE_EDITOR/TEMPLATE/regform/`: `index.php`,
   `src/bootstrap.php`, `src/mailer.php`, `src/index.php`,
   `registrations/index.php`, `assets/form.js`, `assets/styles.css`.
   Rivedere eventuali personalizzazioni; non sostituire `settings.yaml`,
   destinatari, prezzi, privacy, asset o lo stato di apertura concordato.
   La copia originale dell'Editor non aggiorna automaticamente il PHP di un
   WEBSITE già aperto e non contiene il precedente comando aggiunto per farlo.
   Aprire i sorgenti aggiornati da **Dashboard → Conference Editor**, assegnare
   una nuova versione tecnica ed esportare **WEBSITE**. INFO non serve per
   questa correzione e non va importato implicitamente.
5. Sospendere temporaneamente il modulo e applicare soltanto PHP-FPM:

   ```bash
   sudo mifpctl events-php-disable PLMCN-2027/regform
   sudo mifpctl host-apply php
   sudo php-fpm8.3 -t
   sudo grep -nF 'env[MIFP_REGISTRATION_READ_GROUP]' /etc/php/8.3/fpm/pool.d/mifp-events.conf
   ```

   Deve comparire il valore `1`. Se revoca, validazione o reload falliscono,
   non sostituire il WEBSITE. `host-apply php` non modifica il Caddyfile o le
   credenziali SMTP, e conserva le altre autorizzazioni.
6. Dalla dashboard **Conference sites**, sostituire esplicitamente il WEBSITE
   con il nuovo ZIP, usando validazione e rollback esistenti. PHP resta revocato.
   Non sovrascrivere script direttamente sotto una allowlist attiva.
7. Predisporre la lettura del CSV/allegati esistenti e ricreare il container:

   ```bash
   sudo mifpctl registrations-access PLMCN-2027
   sudo mifpctl config-set REGISTRATIONS_DASHBOARD_ENABLED true
   sudo mifpctl restart
   sudo mifpctl mail-diagnose
   ```

   Il helper cambia selettivamente i modi, non i contenuti. Non eseguire
   chmod ricorsivi. Per un evento senza CSV non creare una registrazione fittizia:
   verificare prima lo storage vuoto e lasciare che il runtime aggiornato crei
   il CSV al primo invio reale autorizzato.
8. Verificare i file pubblicati e la lettura dashboard:

   ```bash
   sudo grep -nF 'MIFP_REGISTRATION_READ_GROUP' /srv/mifp-events/PLMCN-2027/regform/src/bootstrap.php
   sudo stat -c '%a %U:%G %n' \
     /srv/mifp-events-private/registrations/PLMCN-2027 \
     /srv/mifp-events-private/registrations/PLMCN-2027/registrations.csv
   ```

   Attesi directory `750`, CSV `640`, owner/gruppo `mifp-events`. Aprire la
   pagina Registrations autenticata: deve mostrare i dati, senza modificarli.
9. Solo dopo revisione del codice e verifiche riuscite, riabilitare il modulo:

   ```bash
   sudo mifpctl events-php-enable PLMCN-2027/regform
   curl --fail --silent --show-error --output /dev/null https://events.mifp.eu/PLMCN-2027/regform/
   sudo mifpctl doctor
   ```

   Riaprire Registrations dopo quel GET: anche una visita al vecchio runtime
   poteva ripristinare `0700`. Non inviare nuove registrazioni o email di prova
   in produzione. POST, upload, doppio invio e mantenimento dei modi sono testati
   su copie sintetiche isolate.

## Recupero

Se una verifica fallisce, mantenere PHP revocato e acquisire il codice diagnostico.
Per la sola immagine usare `sudo mifpctl rollback`. Per il WEBSITE usare il
rollback della dashboard a PHP revocato; una versione vecchia può ripristinare
il difetto dei permessi, quindi non riabilitarla senza averlo valutato. Per il pool
seguire il recupero delle configurazioni salvate nella guida VPS; non ripristinare
alla cieca un vecchio `sendmail_path` difettoso. Non ripristinare il DB o cancellare
iscrizioni per risolvere un problema di lettura.

## Diagnostica read-only di un presunto HTTP 505

I casi acquisiti finora sono **HTTP 503**, con `PermissionError errno 13` dopo
l'invio. Non è stata dimostrata una causa di HTTP 505. La dashboard ora registra
`registrations.storage_unavailable` con ragione sicura e request ID, senza CSV,
indirizzi o eccezioni grezze. Ragioni: `not_connected`, `permission_denied`,
`invalid_data`, `busy`, `storage_error`. Se manca del tutto la configurazione,
`not_configured` distingue la pagina elenco informativa (200) dalle operazioni
non disponibili (503). Un writer bloccato oltre un secondo
produce 503 con `Retry-After: 5`, senza occupare il worker indefinitamente.

Per un nuovo episodio annotare orario UTC, URL senza query personali, metodo e
stato **effettivo** nella scheda Network del browser. Sulla VPS, sostituire gli
orari con una finestra stretta attorno all'episodio:

```bash
sudo journalctl -u caddy -u php8.3-fpm --since '2026-10-09 18:00:00 UTC' --until '2026-10-09 18:05:00 UTC' --no-pager
sudo tail -n 80 /var/log/php-mifp-events.log
cid=$(sudo docker ps -q --filter label=com.docker.compose.project=mifp --filter label=com.docker.compose.service=web)
sudo docker logs --since 10m "$cid" 2>&1 | grep -F 'REQUEST_ID_DA_INSERIRE'
sudo systemctl is-active caddy php8.3-fpm
sudo php-fpm8.3 -t
```

Rivedere e redigere i log localmente prima di condividerli: non inviare cookie,
password, POST, allegati o contenuti personali. Non usare `msmtp --debug`, curl
con dump dei form o registrazioni reali per la diagnosi. Una risposta 505, se
rilevata, richiede queste evidenze prima di modificare protocolli o timeout.

## Cancellazione dalla dashboard ed export protetti

CSV ed Excel contengono la lista filtrata completa, con metadati. Word e PDF
contengono la lista sintetica leggibile e poi copie delle ricevute: tutte le
pagine PDF e immagini JPEG/PNG, con codice iscrizione, partecipante e titolo.
Le copie sono rasterizzate e non contengono script PDF. Un allegato mancante,
non valido o cifrato compare come avviso; non viene presentato come incluso.
Tutti gli export richiedono di reinserire la password amministratore, via POST
con CSRF. Il file scaricato non è cifrato con quella password: la password
**autorizza l'operazione**, come le altre azioni sensibili della dashboard.

Export e cancellazioni mostrano il totem condiviso con barra di avanzamento.
Durante l'elaborazione server la barra è indeterminata; durante la ricezione
del documento la percentuale appare solo se la dimensione è nota. Il 100%
indica che il documento è pronto e il download è stato avviato, non il
salvataggio su disco. Le cancellazioni usano la navigazione ordinaria e non
vengono ritentate automaticamente. Tutti gli asset sono locali; `pypdfium2`
è una dipendenza Python installata nell'immagine, senza CDN.

I limiti dei documenti sono 2.000 iscrizioni, 100 pagine di ricevute,
10 MiB per allegato e 64 MiB complessivi di originali/immagini normalizzate.
Se superati, l'export viene rifiutato: restringere i filtri. CSV/Excel conservano
il limite di 50.000 righe e la protezione dalle formule. Word/PDF usano i colori
e gli esportatori istituzionali della dashboard. La conversione delle ricevute
usa [pypdfium2](https://pypdfium2.readthedocs.io/en/stable/python_api.html),
versione fissata nel lock; non occorrono nuovi servizi sulla VPS.

La cancellazione effettiva è disponibile nella lista (selezione singola,
più righe o tutte) e nel dettaglio. Richiede password, CSRF e conferma della
chiave evento. “Tutte” include gli archiviati e ignora i filtri. Il CSV mantiene
header e inode: si eliminano le righe, non il file usato da PHP. Sono rimossi
anche ricevute e stati delle notifiche delle sole iscrizioni selezionate. I lock
tecnici restano stabili come nelle operazioni host esistenti.
Note e storico amministrativo non vengono riscritti nel CSV.

L'alert va all'amministratore configurato nel sistema notifiche esistente,
non ai partecipanti. Per questa operazione è obbligatorio anche se gli alert
ordinari sono disattivati: se il trasporto non accetta l'alert iniziale,
**nessuna cancellazione viene eseguita**. I backend email `disabled` e `console`
non autorizzano la cancellazione. Segue un alert di completamento o
fallimento. Accettazione SMTP non significa consegna in casella. Un fallimento
del solo alert finale viene segnalato senza ripetere la cancellazione.

### Abilitazione esplicita della scrittura sulla VPS esistente

La modalità predefinita resta in sola lettura. Solo per la cancellazione
richiesta si rendono scrivibili i due mount già esistenti `registrations/` e
`uploads/`, con gruppo PHP, per gli eventi predisposti. Nessun nuovo mount,
credenziale, sessione, socket o configurazione host è esposto alla webapp.
La root del container resta read-only e il processo resta non-root.

Dopo backup, refresh host tools e aggiornamento Docker descritti sopra:

1. Nei sorgenti locali del WEBSITE aggiornare **anche nell'altro progetto
   Editor** `TEMPLATE/regform/index.php` e `TEMPLATE/regform/src/bootstrap.php`
   dalle copie versionate in `MIFPAPP/CORE/CONFERENCE_EDITOR/TEMPLATE/regform/`.
   Per PLMCN-2027 applicare le stesse modifiche al suo `regform/`, preservando
   personalizzazioni, impostazioni e dati. L'Editor conserva i file di un
   progetto esistente: aprirlo ed esportarlo non aggiorna automaticamente il PHP.
2. Esportare il nuovo WEBSITE. Revocare PHP, quindi sostituire il WEBSITE
   dalla dashboard. Non sovrascrivere un modulo autorizzato:

   ```bash
   sudo mifpctl events-php-disable PLMCN-2027/regform
   ```

3. Dopo la sostituzione, abilitare e applicare soltanto PHP e i permessi
   dell'evento. Ogni comando deve riuscire prima del successivo:

   ```bash
   sudo mifpctl config-set REGISTRATIONS_DASHBOARD_ENABLED true
   sudo mifpctl config-set REGISTRATIONS_DASHBOARD_WRITE_ENABLED true
   sudo mifpctl host-apply php
   sudo php-fpm8.3 -t
   sudo mifpctl registrations-access PLMCN-2027
   sudo mifpctl restart
   ```

   Il helper rifiuta l'accesso in scrittura se PHP non è revocato o il runtime
   pubblicato non contiene supporto ai permessi e lock delle operazioni.
   Ripetere `registrations-access` per ogni evento che deve essere scrivibile;
   non eseguire chmod ricorsivi. Se non esiste ancora il CSV, non creare una
   registrazione fittizia: attendere un invio reale autorizzato prima del helper.
4. Verificare senza creare nuove iscrizioni o inviare email di test:

   ```bash
   sudo grep -nF 'env[MIFP_REGISTRATION_WRITE_GROUP] = 1' /etc/php/8.3/fpm/pool.d/mifp-events.conf
   sudo stat -c '%a %U:%G %n' \
     /srv/mifp-events-private/registrations/PLMCN-2027 \
     /srv/mifp-events-private/registrations/PLMCN-2027/registrations.csv \
     /srv/mifp-events-private/registrations/PLMCN-2027/.operations.lock \
     /srv/mifp-events-private/uploads/PLMCN-2027
   ```

   Attesi directory evento `2770`, CSV/lock `660`, gruppo `mifp-events`.
   Controllare lettura ed export nella dashboard. Solo dopo la revisione:

   ```bash
   sudo mifpctl events-php-enable PLMCN-2027/regform
   sudo mifpctl status
   ```

Non applicare Caddy, SMTP o `host-apply all` per questa funzionalità. Gli
aggiornamenti ordinari conservano l'opt-in; senza opt-in i mount restano RO.
Per tornare in sola lettura: revocare PHP, impostare
`REGISTRATIONS_DASHBOARD_WRITE_ENABLED false`, applicare `host-apply php`,
eseguire `registrations-access PLMCN-2027`, poi `restart` e riapprovare PHP.
In locale l'equivalente è `REGISTRATIONS_WRITE_ENABLED=true`, sempre con
password e trasporto alert configurato; non vengono recuperati dati della VPS.

### Nessuna copia automatica e gestione degli errori

Le cancellazioni dalla dashboard non creano ZIP o copie persistenti del CSV,
delle ricevute o degli stati email. Le copie eventualmente già presenti non
vengono eliminate da questa modifica. I backup generali della VPS e le
operazioni host preesistenti restano invariati.

Solo durante la richiesta vengono tenuti in memoria i dati necessari a tentare
l'annullamento di errori intercettati: massimo 96 MiB per operazione. Il piccolo
registro tecnico `0600` sotto
`/opt/mifp/data/conferences/.registrations-admin/deletion-operations/` contiene
solo evento, numero di record, identificativo operazione ed esito, senza CSV,
nominativi o ricevute. Un'operazione interrotta o un annullamento fallito blocca
ulteriori cancellazioni (`recovery_required`), per evitare di ripetere una
modifica parziale.

Il recupero dopo un arresto del processo è manuale: revocare PHP, fermare la
webapp con il workflow esistente, verificare CSV e allegati correnti e
riconciliarli con i propri originali, preservando gli invii successivi. Solo
dopo la verifica dei dati e dei permessi riconciliare il registro tecnico e
riattivare i servizi. Non è garantito un ripristino completo in caso di arresto.

### Export e import manuale delle registrazioni

Il pulsante **Recovery ZIP + originals** scarica su richiesta un pacchetto
privato con CSV originale, ricevute originali e manifest con evento/versione e
SHA-256. Segue i filtri correnti: azzerare i filtri e includere gli archiviati
per esportare tutto. Non viene conservato alcuno ZIP sul server. Se manca una
ricevuta originale, l'export ZIP viene rifiutato invece di produrre un pacchetto
incompleto. Limiti: 5.000 righe, 10 MiB per ricevuta e 96 MiB complessivi.
Il CSV interno conserva i valori originali ed è destinato al ripristino; per
aprire dati in un foglio di calcolo usare CSV/Excel ordinari, protetti dalle
formule. Il pacchetto contiene dati personali e va conservato privatamente.

**Import registrations** accetta questo ZIP oppure un CSV UTF-8, con password,
CSRF, conferma evento, autorizzazione host alla scrittura e alert obbligatorio
all'amministratore. Non occorrono ulteriori mount o flag rispetto a quelli
previsti per la cancellazione. L'evento deve avere già directory private, lock
e CSV inizializzati dal runtime PHP; non viene creato un evento dalla webapp.

L'import aggiunge soltanto record mancanti, salta quelli identici, ripristina
le ricevute mancanti e rifiuta conflitti con record/file esistenti. Non cancella
o sovrascrive invii successivi. Le colonne devono corrispondere al CSV del
modulo corrente; le colonne amministrative degli export di consultazione sono
ignorate. Il CSV ordinario può contenere gli apostrofi aggiunti per neutralizzare
formule: per un ripristino esatto dei valori usare Recovery ZIP. CSV senza ZIP
richiede che gli allegati referenziati siano già presenti sul server.

Gli ZIP vengono letti senza estrazione su disco: nomi inattesi, duplicati,
symlink, altro evento, checksum errati, contenuti non supportati e limiti
superati vengono rifiutati. Il lock PHP coordina import, cancellazioni e nuovi
invii. Nessuna email viene inviata ai partecipanti e non vengono importati stati
email, sessioni, autorizzazioni PHP o codice. I metadati amministrativi già
presenti restano; note/storico non sono contenuti nel pacchetto.

In caso di errore intercettato viene tentato l'annullamento in memoria, senza
backup automatici. Un arresto o annullamento fallito blocca ulteriori modifiche:
prima di un nuovo import riconciliare dati e registro tecnico come descritto
sopra. Il completamento dell'import compare negli alert e nel log audit, senza
registrare dati dei partecipanti.

WEBSITE/INFO continua a non importare iscrizioni: questa è un'azione separata
ed esplicita nella pagina Registrations. Word/PDF restano documenti di
consultazione; il ripristino con originali richiede Recovery ZIP.
