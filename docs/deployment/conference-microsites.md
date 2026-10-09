# Conference microsites: correzioni VPS e ripubblicazione

La pubblicazione resta quella esistente: Caddy serve `/srv/mifp-events`, Flask
pubblica tramite il mount `/app/event-sites` e PHP-FPM esegue soltanto i moduli
regform autorizzati dall'operatore. Nessun nuovo servizio è necessario.

## Difetti verificati e correzioni

- Il matcher PHP accettava solo URL `.php`, escludendo il link `regform/`.
  L'include generato ora riscrive soltanto la directory approvata a `index.php`.
  L'URL senza slash riceve un redirect 308 che conserva la query. Gli script
  mancanti restituiscono 404, senza fallback a script di altri micrositi.
- Le protezioni su `src/`, `registrations/`, impostazioni e dotfile precedono
  la riscrittura. Il blocco generico PHP copre anche i suffissi PATH_INFO.
  Gli altri siti e gli asset statici conservano il loro comportamento.
- L'allowlist host era legata al percorso, non alla versione. Il publisher
  `local-vps` blocca pubblicazione e restore che coinvolgono un prefisso
  autorizzato, anche per un pacchetto identico o statico. Serve prima una revoca
  esplicita; il container non riceve poteri per amministrare Caddy.
- Il lock e i prefissi protetti sono condivisi in una directory dedicata,
  montata in sola lettura. La dashboard non usa questa policy per dedurre lo
  stato operativo PHP: un prefisso protetto può anche rappresentare un blocco
  conservativo dopo un reload fallito. Credenziali, dati privati e allowlist
  canonica dell'host non sono montati.
- Gli errori durante copia/switch ripristinano il sito precedente senza
  riabilitare PHP. La pulizia delle vecchie copie avviene dopo lo switch:
  un errore di pulizia non può cancellare il nuovo sito dopo aver eliminato il
  precedente. Eventuali copie residue restano nascoste e non servite da Caddy.
- Dashboard, wizard e messaggi non dichiarano più `PHP disabled` come stato
  operativo. Il publisher VPS è descritto come hosting Caddy sulla stessa VPS;
  lo stato PHP reale resta non disponibile nell'applicazione.

## Limiti upload e apertura iscrizioni

Il pool dedicato mantiene `upload_max_filesize=10M` e `post_max_size=12M`.
Non ampliare i limiti globali per adattarli a un singolo pacchetto. Il modulo
PHP deve usare il minore tra limite configurato, limite PHP per file e limite
POST, lasciando spazio per multipart e altri campi. Interfaccia, JavaScript e
validazione lato server devono esprimere lo stesso limite effettivo.

Finché tariffe e condizioni non sono confermate, mantenere il modulo visibile
con `submit_enabled: false`. L'autorizzazione PHP non apre gli invii.

Prima dell'apertura, confermare prezzi, tasse, scadenze, rimborsi, dettagli di
pagamento e informativa privacy. Controllare che le descrizioni dei cookie
corrispondano al pool PHP effettivo. Non inventare decisioni organizzative o
contenuti legali definitivi.

## Verifica locale

I test HTTP usano Caddy/PHP-FPM temporanei e non i servizi installati.
Richiedono i binari indicati; quelli sul WEBSITE richiedono anche una copia del
sorgente. Le dipendenze assenti sono segnalate come skip.

```bash
MIFP_TEST_CADDY=/path/to/caddy \
MIFP_TEST_PHP_FPM=/path/to/php-fpm8.3 \
MIFP_TEST_PLMCN_SOURCE=/path/to/PLMCN-2027 \
.venv/bin/python -m pytest -n 0 -q \
  TESTS/webapp/test_events_caddy_runtime.py \
  TESTS/webapp/test_plmcn_regform_runtime.py
```

I test usano storage, socket, porte e certificati temporanei. Le email vengono
bloccate o catturate localmente, senza consegna reale. Gli upload sono sintetici
e non vengono create registrazioni reali. Per una verifica equivalente alla VPS,
usare PHP-FPM 8.3.

## Interventi manuali sulla VPS

Questi passi sono interventi espliciti dell’operatore, fuori dalla pipeline di test.

1. Aggiornare il bundle host versionato tramite `refresh-host-tools.sh` e usare
   il Compose aggiornato al successivo deploy/restart esplicito. Il normale
   deploy prepara la policy dalle autorizzazioni esistenti prima di avviare il
   container. Il mount deve essere in sola lettura a `/run/mifp-events-policy`.
2. Rivedere il solo blocco `events.mifp.eu` del Caddyfile attivo: aggiornare il
   matcher generico PHP alla forma `(?i)\.(?:php|phtml|phar|phps|php[0-9]*)(?:/|$)`.
   Conservare gli altri virtual host. Validare il Caddyfile prima di un reload.
   Il nuovo routing della directory viene generato nell'include dai comandi PHP.
3. Mantenere il pool dedicato a 10M/12M; non occorre ampliare i limiti. Verificare
   la configurazione effettiva con gli strumenti PHP-FPM 8.3 della VPS. Non
   modificare SMTP, DNS, credenziali o il trasporto email.

## Ripubblicazione sicura di un sito autorizzato

Gli esempi usano `PLMCN-2027`; sostituire il percorso con quello del sito.

1. Esportare un nuovo `*_WEBSITE.zip` dal sorgente corretto. Verificare che
   includa le modifiche PHP/JS, `submit_enabled: false` e il limite 10 MB.
2. Sulla VPS revocare esplicitamente:

   ```bash
   sudo mifpctl events-php-disable PLMCN-2027/regform
   sudo mifpctl events-php-list
   ```

   Se la revoca o il reload fallisce, fermarsi: la policy conserva il blocco.
3. Importare dalla dashboard il WEBSITE con sostituzione e copia di rollback.
   Se l'import fallisce, PHP resta revocato; verificare il sito precedente e
   correggere/riprovare. Un eventuale restore non riabilita automaticamente PHP.
4. Rivedere il codice pubblicato e la configurazione chiusa, quindi autorizzare
   nuovamente il modulo:

   ```bash
   sudo mifpctl events-php-enable PLMCN-2027/regform
   sudo mifpctl events-php-list
   ```

   Abilitare PHP non apre le iscrizioni: `submit_enabled` resta false.
5. Verificare con richieste GET `/PLMCN-2027/regform/` e
   `/PLMCN-2027/regform/index.php`, il messaggio di iscrizioni chiuse e gli asset.
   Controllare che impostazioni, `src/`, `registrations/`, dotfile e PHP di altri
   siti siano negati. Non inviare registrazioni o email reali come smoke test.
6. Aprire gli invii solo dopo le conferme umane. Anche il successivo aggiornamento
   di `submit_enabled` richiede revoca, nuova pubblicazione e nuova approvazione.
