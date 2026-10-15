# MIFP — Hardening mirato del 15 ottobre 2026

A, B e D corretti; C confermato e rinviato secondo la clausola di compatibilità
della richiesta. MFA non implementata. Nessun commit, deploy, import reale,
migrazione, nuova dipendenza o modifica di dati/configurazioni reali.

## 1. Problemi confermati

- **A, alto:** `services/public_repository.py`, `list_members_page()`,
  `list_member_roles()` e `get_home_context()` filtravano solo `is_active`.
  Membri in bozza/revisione/quarantena potevano apparire nell'elenco, nei filtri
  e nei conteggi pubblici. Anche `routes/public.py`, `about()` e `research()`,
  contavano questi record. `services/seo.py`, `_static_lastmods()`, includeva le
  loro modifiche nelle date pubbliche. La ricerca globale in `services/search.py`
  applicava già correttamente entrambi i criteri: non modificata.
- **B, basso:** `routes/public.py`, `_markdown_html()`, restituiva il testo
  originale su `ImportError`. I template istituzionali lo inseriscono con
  `|safe`: HTML attivo poteva quindi arrivare al browser senza sanificazione.
- **C, alto, ancora aperto:** `routes/public.py`, `media()`, controlla percorso,
  confinamento e presenza del file, ma non l'autorizzazione pubblica. Qualsiasi
  file raggiungibile sotto `ASSETS_DIR` resta scaricabile conoscendone l'URL,
  anche se usato soltanto da una bozza.
- **D, alto:** `config.py` imposta normalmente 1032 MiB per richiesta;
  `__init__.py`, `validate_csrf()`, accedeva a `request.form` senza un limite
  pubblico distinto. Richieste anonime potevano beneficiare del limite riservato
  agli upload amministrativi prima dei controlli delle singole route.

## 2. Modifiche effettuate

Percorsi applicativi relativi a `MIFPAPP/CORE/mifp_app/`:

| File | Modifica |
| --- | --- |
| `services/public_repository.py` | Predicato `is_active=1 AND review_status='published'` per elenco, ricerca locale, paginazione, ruoli, membri e paesi della homepage. |
| `routes/public.py` | Stesso predicato nelle statistiche About e Research; escaping del fallback Markdown con MarkupSafe già disponibile. |
| `services/seo.py` | Date dei membri nella sitemap calcolate soltanto sui record pubblici. |
| `config.py` | `PUBLIC_MAX_CONTENT_LENGTH = 64 * 1024`; limite globale degli upload invariato. |
| `__init__.py` | Hook prima di CSRF e maintenance gate; limiti per endpoint pubblico, esenzione dashboard allineata alla scadenza amministrativa; errore 413 e log riferiti al limite effettivo. |
| `routes/public_polls.py` | Rimossi i due limiti tardivi: exchange e submit sono limitati dal preflight prima di lettura e CSRF. |

Nuovi file: `TESTS/webapp/test_targeted_security_hardening.py` e questo rapporto.

Il limite di 64 KiB copre `/login`, `/join`, `/logout` e anche scritture anonime
verso dashboard, route inesistenti e altri endpoint, con due eccezioni esplicite
basate sull'endpoint Flask: `public.respond_exchange` usa 4096 byte e
`public.respond_submit` usa 512 KiB. Nessuna eccezione usa prefissi URL.
In ogni caso il limite effettivo è il minimo fra limite specifico e globale.
I campi di Join comprendono testi brevi e
una motivazione di 2500 caratteri: 64 KiB lasciano margine per UTF-8, codifica URL
e overhead multipart. Anche gli amministratori mantengono il limite piccolo
sulle route pubbliche. L'esenzione dashboard considera `admin_login_at` e
`ADMIN_SESSION_HOURS` con la stessa condizione di scadenza di `login_required`.
Una sessione scaduta riceve 64 KiB durante il preflight, senza essere modificata:
la gestione della scadenza resta al decoratore di autenticazione. Il comportamento
preesistente per timestamp assente/zero resta allineato al decoratore.

Con `Content-Length` eccessivo il rifiuto precede lettura e parsing. Per le
scritture restanti il body viene letto e conservato attraverso lo stream limitato
di Werkzeug, prima del parsing: form e JSON possono riutilizzarlo. Questo evita
anche che un parser multipart accetti un modulo ignorando una coda eccessiva.
Gunicorn imposta `wsgi.input_terminated`: i trasferimenti chunked sono quindi
limitati anche senza lunghezza dichiarata. Quando la lunghezza è sconosciuta,
raggiungere esattamente il limite effettivo produce 413: `LimitedStream.readall()`
può fermarsi al limite senza provare EOF. Con lunghezza nota è ammesso il valore
esatto del limite (4096, 65536 o 524288 byte, salvo tetto globale inferiore).
Senza lunghezza né indicazione di terminazione resta il fallback sicuro di
Werkzeug: nessuna lettura illimitata, nessun form disponibile per superare CSRF.

Il formato JSON 413 conserva `error`, `message`, `max_mb`, `request_id` e aggiunge
`max_bytes`. I messaggi e l'audit specifico degli import autenticati restano
invariati; gli errori pubblici indicano il limite in KiB.

Riferimenti verificati: [limiti per richiesta in Flask 3.1](https://flask.palletsprojects.com/en/stable/api/#flask.Request.max_content_length)
e [stream WSGI di Werkzeug](https://werkzeug.palletsprojects.com/en/stable/wsgi/#werkzeug.wsgi.get_input_stream).
Controllato anche il codice delle librerie installate, incluso
`gunicorn.http.wsgi.base_environ()`.

## 3. Interventi non effettuati: diagnosi C e proposta separata

Ricostruzione effettuata su sorgenti, schema e fixture sintetiche; nessun
inventario o cambiamento dei dati reali.

| Uso | Riferimenti e criterio pubblico corrente |
| --- | --- |
| Foto membri | `list_members_page()`: `asset_links`, ruoli profile/cover/gallery/attachment; membro attivo e pubblicato dopo A. |
| News | `news_asset_info()`/`enrich_news()`: cover/gallery/document e compatibilità attachment; news pubblicata. |
| Pubblicazioni e ricerca | `list_public_publications()`/`list_public_research()`: documenti PDF e immagini tramite collegamenti; entità pubblicata. |
| Eventi | `enrich_event()`/`event_documents()`: cover/logo/gallery/attachment/document; evento pubblicato. |
| Archivio | `get_archive_entry()`: asset dell'evento; stato editoriale ereditato dall'evento, non dall'entry archivio. |
| Istituzionali | Pagine pubblicate in DB, Markdown e fallback impacchettati; URL possono essere incorporati nel testo renderizzato. |
| Sponsor/loghi | `list_home_sponsors()`/`get_public_sponsor()`: collegamenti logo/attachment/gallery; solo `is_active`, nessun `review_status` nello schema sponsor. |
| Asset condivisi | `asset_links` ammette più entità per asset: una relazione privata non deve annullare una relazione legittimamente pubblica. |
| Dashboard | `/dashboard/assets/<path>` usa `login_required`; ricerca/creazione asset generano questi URL autenticati. |
| Conference authoring | `conference_assets` e `/dashboard/conferences/<id>/assets/<filename>` sono un percorso separato, autenticato; pubblicazione WEBSITE tramite publisher, non autorizzazione di `/media`. |
| Import/legacy | `resolve_db_asset_path()` accetta prefisso astratto `assets/`, nome della directory configurata e percorsi relativi; `_existing_asset_path()` conserva ulteriori fallback legacy. |
| Asset senza collegamenti | Possono essere risorse legittimamente renderizzate, file amministrativi oppure orfani; `assets` non contiene un attributo di visibilità. |

**Impedimento verificabile:** `enrich_event()` cerca deliberatamente immagini
senza relazione esplicita tramite un segmento esatto dello slug in `source_url`.
Il test esistente
`test_assets_and_news.py::test_event_uses_exact_slug_source_image_when_asset_link_is_missing`
richiede che questa immagine diventi una URL `/media/image/event.png`.
Un controllo `EXISTS(asset_links JOIN entità pubblicata)` negherebbe proprio
questa risorsa. Inoltre HTML/Markdown e `entity_links.url` possono contenere
riferimenti diretti senza corrispondenti righe in `asset_links`.
Il renderer mantiene anche `cover_url()` per record legacy con `cover_asset_id`,
che non è una colonna delle attuali tabelle di contenuto: non va scambiato per
una relazione corrente garantita dallo schema.

Autorizzare tutti gli asset senza link riprodurrebbe il difetto. Usare la
provenienza remota come permesso replicherebbe un'euristica di rendering come
regola di sicurezza, senza risolvere i file amministrativi/orfani o i riferimenti
testuali. Le fixture non classificano tutti gli eventuali file storici reali:
non è verificabile una negazione predefinita compatibile su questa base.
Nessun controllo parziale è stato introdotto; C resta esplicitamente aperto.

Proposta minima separata, da completare prima di attivare il controllo:

1. Inventario di sola lettura dei percorsi normalizzati, collegamenti espliciti,
   fallback per provenienza e riferimenti HTML/Markdown/URL; elenco dei casi
   ambigui da classificare, inclusi file non presenti in `assets`.
2. Associare le risorse pubbliche dimostrate alle entità corrette attraverso
   `asset_links` già esistente, con manutenzione esplicita e verificata; decidere
   la destinazione dei file standalone prima di negare quelli senza legami.
   Niente spostamenti o cambi di URL, né migrazione proposta in questa sessione.
3. Introdurre in `/media` la verifica di almeno un legame a un'entità pubblica:
   published per eventi/news/pagine/pubblicazioni/ricerca, active+published per
   membri, active per sponsor. Risolvere tutti gli alias di percorso verso lo
   stesso file prima della decisione; mantenere la route dashboard autenticata.
4. Verificare pubblicato, bozza, condiviso, orfano, legacy, allegati, symlink e
   revoca dopo cambio di stato. Gestire anche le copie già in cache pubblica.

E escluso come richiesto: nessun MFA o nuovo sistema di identità.

## 4. Test e controlli

### Revisione locale dello Step D

La revisione indipendente successiva ha identificato una regressione non coperta
dalla prima verifica: il caricamento anticipato a 64 KiB ignorava i limiti delle
POST sondaggi. Bloccava submit legittime oltre 64 KiB e lasciava exchange leggere
fino a 64 KiB prima dell'assegnazione tardiva di 4096 byte. Inoltre una sessione
amministrativa firmata ma scaduta conservava l'esenzione fino al decoratore.

Correzione circoscritta a `__init__.py`, `routes/public_polls.py`,
`TESTS/webapp/test_targeted_security_hardening.py` e questo rapporto. A, B, C,
autenticazione, validazioni dei sondaggi e infrastruttura non modificati.

Comando finale della revisione:

```bash
.venv/bin/python -m pytest -o addopts='' \
  TESTS/webapp/test_targeted_security_hardening.py \
  TESTS/webapp/test_polls.py TESTS/webapp/test_auth.py TESTS/webapp/test_csrf.py \
  -q --tb=short
```

**89 test passati in 8,05 secondi**, nessuno skip. `git diff --check`: OK.
Non rieseguita la suite rapida completa: i test mirati coprono i percorsi modificati.

Evidenze:

- **4 KiB:** JSON exchange oltre 4096 byte respinto con 413 e `max_bytes=4096`,
  anche con CSRF valido e stream chunked senza `Content-Length`. Un controllo
  nel test impedisce che venga raggiunto il parsing dei form usato da CSRF.
- **64 KiB:** richieste anonime ordinarie e URL simili a submit mantengono 413
  oltre 65536 byte. Testati anche i confini, streaming e assenza di terminazione.
- **512 KiB:** due submission valide di circa 70 KiB ricevono 200 e le risposte
  salvate coincidono con quelle inviate, sia con lunghezza nota sia chunked.
  I dati sintetici usano 14 domande testuali con 5000 caratteri ciascuna,
  rispettando le validazioni esistenti, senza campi artificiali ignorati.
  Oltre 524288 byte il preflight restituisce 413 con CSRF valido.
- **Tetto globale:** con limite globale sintetico di 2048 byte, entrambi gli
  endpoint sondaggi restituiscono 413 con `max_bytes=2048`.
- **Sessioni:** upload dashboard da 128 KiB consentito con sessione attiva;
  stessa dimensione respinta a 64 KiB con sessione scaduta secondo una durata
  configurata di due ore. Il preflight lascia intatta la sessione; una successiva
  richiesta piccola raggiunge `login_required`, che risponde `session_expired`.
- **Senza lunghezza né terminazione:** nessuna lettura dello stream illimitato
  per entrambi gli endpoint sondaggi; richiesta priva di payload utile respinta.

I test chunked lavorano sullo stream WSGI decodificato; non sono prove di rete
contro Caddy o produzione. Nessun nuovo componente, commit o deploy.

### Verifica iniziale di hardening, prima della revisione D

Comandi e risultati storici, da non confondere con la verifica mirata sopra:

- `.venv/bin/python -m pytest -o addopts='' TESTS/webapp/test_targeted_security_hardening.py -q`:
  31 passati nella verifica mirata; i due test aggiunti successivamente per
  ricerca globale e statistiche About/Research sono inclusi nella suite finale.
- `MIFP_TEST_AUTO_INSTALL=0 bash test_all.sh --suite quick -- -q --tb=short`:
  **1371 passati, 38 saltati** (webapp: 1342 passati/38 saltati;
  database: 22 passati; strumenti: 7 passati). Eseguita con socket locali
  consentiti, sempre con storage temporaneo e servizi di test.
- `python3 tools/check_repo_hygiene.py`: OK, 528 file Git controllati.
- `.venv/bin/python tools/security_audit.py audit --strict`: 22 controlli OK.
- `git diff --check`: OK.
- `.venv/bin/python tools/security_audit.py dependencies`: saltato esplicitamente
  dal tool perché `pip-audit` non è installato. Nessuna dipendenza installata o
  modificata; il controllo delle dipendenze resta previsto in CI.

La prima suite rapida nel sandbox aveva riportato 1253 passati, 38 saltati e
87 falliti. La diagnosi mirata ha riprodotto `PermissionError` sulle socket
locali nelle fixture deploy/registrazioni/PHP e individuato una regressione nel
messaggio 413 con limite amministrativo sintetico di 256 byte. Il messaggio è
stato ripristinato e il test specifico è passato. La seconda suite rapida era
giustificata da questa correzione e dal vincolo ambientale: ha superato tutti
i test eseguibili, senza modificare il deploy o aggirare controlli applicativi.

I 38 skip restano esclusioni della suite locale: non sono conteggiati come test
passati. Non eseguita la suite browser separata né una verifica sulla produzione.

I nuovi test verificano tutti gli stati dei membri (anche duplicate), inattivi,
conteggi, ruoli, paginazione, ricerca pubblica e amministrativa, About/Research,
fallback Markdown, rendering normale sanificato, 413 prima della lettura/parsing,
CSRF valido/invalido, JSON, streaming senza lunghezza, limiti di confine, coda
multipart, login normale, form Join, upload dashboard oltre il limite pubblico,
download autenticato PDF e symlink fuori root. Non attestano che C sia risolto.

Il test Join nuovo usa il percorso honeypot per evitare invii: i test esistenti
di Join verificano le richieste legittime con servizi di notifica controllati.

## 5. Compatibilità

Dashboard e ricerca amministrativa continuano a gestire tutti gli stati.
Login, sessioni, token CSRF, controllo Origin/Referer, rate limiting, hashing,
decoratori di autorizzazione e audit conservano la logica esistente.
Gli upload autenticati mantengono il limite globale; scraper, schema SQLite,
CLI, import/export, asset, backup e contratto di deploy non sono stati modificati.
Per C sono preservati URL, PDF/SVG attachment e controlli di confinamento;
questa compatibilità non costituisce una correzione della visibilità.

## 6. Rischi residui e limiti

- C: risorse private ancora scaricabili da `/media`; il nome non è un permesso.
  Le risposte media possono inoltre essere conservate in cache pubblica fino a
  un'ora: una futura revoca deve considerare anche questo comportamento.
- I test WSGI di streaming usano body sintetici già decodificati: non simulano
  l'intero trasporto HTTP Caddy→Gunicorn e non misurano resistenza a DoS.
- L'audit statico del repository verifica la presenza di controlli; non dimostra
  l'assenza di vulnerabilità, come mostra C ancora aperto.

## 7. Operazioni per il rilascio

Normale build dell'immagine attraverso CI/CD e rilascio manuale con la procedura
esistente. Non eseguiti in questa sessione. Nessun cambio richiesto a variabili
ambiente, volumi, database o configurazioni reali.

Caddy valutato e lasciato invariato: un cap su `/login` e `/join` da solo
lascerebbe anonime le richieste dashboard con limite elevato; un cap globale
romperebbe gli import. La distinzione basata sulla sessione firmata appartiene
all'applicazione. Il controllo anticipato copre quindi l'intera superficie
applicativa pertinente senza introdurre matcher proxy divergenti. Nessuna
modifica Caddy da validare o distribuire per questa patch.

## 8. Stato Git

Nel workspace complessivo risultano modificati i sei file applicativi elencati
al punto 2; aggiunti test e rapporto. In questa revisione D sono stati toccati
soltanto `__init__.py`, `routes/public_polls.py`, test mirati e rapporto.
Le cancellazioni iniziali di `docs/audit/MIFP_MAINTAINABILITY_REVIEW.md`,
`docs/audit/MIFP_PRODUCTION_SECURITY_AUDIT.md` e
`docs/audit/MIFP_PRODUCTION_SECURITY_AUDIT.pdf` appartenevano già al workspace e
non sono state toccate. Nessun commit creato.
