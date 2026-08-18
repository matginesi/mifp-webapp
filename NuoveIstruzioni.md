# Prompt / specifica: sistema MIFP di pulizia, merging intelligente e accorpamento

## Ruolo

Agisci come senior Python engineer e data-quality engineer sulla webapp MIFP.

Devi **implementare nella webapp** un sistema comprensibile, deterministico, sicuro e reversibile per:

- analizzare la qualità del database;
- trovare duplicati esatti e quasi-duplicati;
- individuare record parziali, frammenti, pagine tecniche e dati incoerenti;
- proporre cluster di merge;
- mostrare chiaramente perché due record sono considerati equivalenti;
- fondere le informazioni complementari in una sola entità canonica;
- permettere dry-run, revisione, applicazione e rollback;
- produrre audit completo.

Codex deve modificare il codice della webapp. **Non deve ripulire direttamente il database durante lo sviluppo e non deve sostituirsi alla webapp nell'analisi operativa.** La pulizia reale deve essere eseguita dalla webapp tramite le regole implementate.

Non fare overengineering. Non introdurre microservizi, code distribuite, framework pesanti o dipendenze non necessarie.

---

# 1. Principi non negoziabili

## 1.1 Separare candidate generation e decisione

Il sistema deve avere due livelli distinti:

1. **Candidate generation**  
   Recupera coppie o gruppi potenzialmente simili usando chiavi normalizzate, blocking, fuzzy matching, URL, DOI, date e, facoltativamente, embedding.

2. **Merge decision**  
   Decide se:
   - `auto_merge`;
   - `suggest_review`;
   - `keep_separate`;
   - `reject_as_junk`.

## 1.2 Fail closed

In caso di dubbio:

- non unire;
- creare un suggerimento di revisione;
- mostrare i conflitti;
- conservare entrambi i record.

È preferibile lasciare due record ambigui piuttosto che fondere due entità realmente diverse.

## 1.3 Idempotenza

Eseguire la stessa analisi due volte sul medesimo database già pulito deve produrre:

- zero nuovi merge automatici;
- nessuna modifica agli slug;
- nessuna duplicazione di link o asset;
- stesso risultato e stesso fingerprint del piano.

## 1.4 Reversibilità

Ogni applicazione deve creare:

- snapshot o backup transazionale;
- `merge_run`;
- piano immutabile;
- elenco delle entità sorgente;
- record canonico prima e dopo;
- campi scelti e campi scartati;
- motivazioni;
- utente e timestamp;
- operazione di rollback.

Non cancellare fisicamente subito i record sorgente. Usare inizialmente uno stato tipo `merged_into`, tombstone o tabella di mapping, quindi eliminazione definitiva solo tramite azione amministrativa separata.

---

# 2. Modello concettuale

Usare una pipeline semplice:

```text
Inventory
  -> Normalize
  -> Validate
  -> Block candidates
  -> Score evidence
  -> Build safe clusters
  -> Resolve canonical fields
  -> Preview diff
  -> Apply transaction
  -> Verify invariants
  -> Audit / rollback
```

Ogni candidato deve avere evidenze strutturate, non una singola percentuale opaca.

Esempio:

```json
{
  "entity_type": "news",
  "left_id": 12,
  "right_id": 173,
  "decision": "auto_merge",
  "confidence": 0.98,
  "evidence": [
    {"kind": "normalized_title_exact", "weight": 0.40},
    {"kind": "same_year", "weight": 0.10},
    {"kind": "body_similarity", "value": 0.94, "weight": 0.20},
    {"kind": "shared_asset_hash", "weight": 0.20},
    {"kind": "no_hard_conflict", "weight": 0.10}
  ],
  "conflicts": []
}
```

La confidence serve per ordinare e presentare. Le regole hard hanno la precedenza sul punteggio.

---

# 3. Normalizzazione

Creare funzioni Python piccole, testabili e specifiche.

## 3.1 Testo

Per il confronto, non per la visualizzazione:

- Unicode NFC/NFKD;
- rimozione di diacritici solo nella chiave di confronto;
- lowercase;
- HTML entity decode;
- rimozione zero-width e control characters;
- spaziatura normalizzata;
- apostrofi e virgolette equivalenti;
- trattini equivalenti;
- punteggiatura non significativa rimossa;
- `&` confrontato con `and`;
- nessuna alterazione del testo originale conservato.

Creare almeno:

```python
normalize_identity_text()
normalize_person_name()
normalize_title()
normalize_whitespace()
```

Non usare una sola funzione aggressiva per tutti i campi.

## 3.2 Slug

Lo slug non è un identificatore affidabile perché può avere suffissi `-2`, `-3` o essere stato rigenerato.

Per il confronto:

- rimuovere soltanto suffissi tecnici comprovati;
- non rimuovere numeri che rappresentano anno, edizione o parte reale del titolo;
- rigenerare lo slug solo dopo la scelta del record canonico;
- assicurare unicità per tipo;
- in collisione usare anno/data e solo dopo un contatore.

## 3.3 URL

Normalizzare una chiave URL senza distruggere l'URL originale:

- host lowercase;
- rimozione fragment;
- rimozione tracking query;
- porta standard;
- slash finali equivalenti;
- HTTP e HTTPS considerabili equivalenti solo sullo stesso host/path;
- percent-decoding sicuro;
- normalizzazione delle trasformazioni Aruba `media/.../v1/...`;
- riconoscimento degli URL wrapper `old.mifp.eu/www.mifp.eu/...`;
- non fondere URL con path realmente differenti.

## 3.4 DOI ed email

- DOI: lowercase, rimozione prefisso `doi:` e `https://doi.org/`.
- Email: lowercase e validazione sintattica.
- DOI uguale è un'identità forte per le pubblicazioni.
- Email uguale è un'identità forte per i membri, salvo indirizzi generici condivisi.

## 3.5 Asset

Creare fingerprint usando in ordine:

1. hash del file, se disponibile;
2. media identifier nell'URL;
3. path normalizzato;
4. URL normalizzato;
5. fallback prudente su nome, dimensione e tipo.

Non deduplicare due immagini solo perché hanno lo stesso nome generico.

---

# 4. Blocking: evitare confronti quadratici

Non confrontare ogni record con ogni altro.

Creare blocchi per tipo:

## member

- cognome normalizzato;
- iniziale nome + cognome;
- email;
- token set del nome;
- eventuale affiliazione/country.

## news

- token significativi del titolo;
- anno/data;
- URL o asset condiviso;
- hash del corpo normalizzato;
- named entities principali, se disponibili.

## event

- acronimo/series;
- anno;
- titolo normalizzato;
- host/path dell'evento;
- intervallo data;
- località.

## publication

- DOI;
- titolo normalizzato;
- anno;
- autori;
- PDF condiviso.

## research_area e sponsor

- nome/titolo normalizzato;
- URL;
- token set completo.

Il blocking deve essere indicizzato e avere limiti configurabili in YAML.

---

# 5. Hard rules di merge e di separazione

## 5.1 Regole forti positive

Esempi:

- stesso DOI valido;
- stessa email personale valida;
- stesso titolo normalizzato e stesso tipo;
- stesso asset hash con titolo altamente compatibile;
- stesso URL canonico di una pagina-entità e titolo compatibile;
- membro con stesso insieme ordinabile di nome/cognome, affiliazione compatibile e nessun conflitto;
- evento con stesso acronimo, stessa edizione/anno e intervallo compatibile.

## 5.2 Hard conflicts

Devono impedire l'auto-merge:

- tipi di entità differenti;
- DOI diversi e validi;
- email personali diverse per membri omonimi;
- anni differenti per eventi della stessa serie;
- intervalli evento incompatibili;
- persone con stesso nome ma affiliazioni incompatibili e nessun'altra evidenza;
- pubblicazioni con titoli simili ma anni/autori/DOI incompatibili;
- news sullo stesso soggetto ma riferite ad azioni diverse;
- stessa serie di conferenza ma edizioni differenti;
- `publication_highlight` e pubblicazione possono essere correlate, ma non sono la stessa entità e non vanno fuse cross-type.

## 5.3 Regole contro substring e token-set falsi

Non considerare duplicati automaticamente:

- `Alexey Kavokin`;
- `Congratulations to Alexey Kavokin`;
- `Alexey Kavokin awarded ...`;
- `International Conference ... 2018`;
- `International Conference ... 2019`.

La presenza del titolo breve dentro quello lungo è solo una candidate signal. Servono data, contenuto, URL, evento o azione coincidente.

---

# 6. Regole specifiche per tipo

## 6.1 Member

Identità primaria:

- nome e cognome corretti;
- email;
- affiliazione;
- country.

Gestire inversioni `Cognome Nome` / `Nome Cognome`.

Il nome canonico deve derivare da `first_name + last_name`, non da un `display_name` storicamente invertito.

Merge campi:

- preferire email valida;
- affiliazione più specifica, non semplicemente più lunga;
- country coerente;
- bio più completa e non duplicata;
- `is_active = 1` se almeno una fonte attendibile è attiva;
- non fondere omonimi senza evidenza aggiuntiva.

## 6.2 News

Segnali:

- titolo normalizzato;
- data o anno;
- corpo/summary;
- URL;
- asset/PDF;
- persone e organizzazioni menzionate;
- tipo della news.

Distinguere notizie diverse sulla stessa persona.

Per il merge:

- scegliere un titolo descrittivo, non un semplice nome;
- conservare il body più completo;
- generare summary dal body soltanto se quello esistente è vuoto, troncato o identico al body;
- unire galleria, cover, documenti e link;
- rimuovere boilerplate come `Read more`, ma non il contenuto;
- mantenere separati accordi diversi del 2016 e del 2018 anche se coinvolgono le stesse organizzazioni.

Date:

- preferire data non inferita;
- preferire precisione day > month > year;
- verificare coerenza con anno del titolo e del contenuto;
- non scegliere un anno storico menzionato nel testo come data della news;
- registrare ogni conflitto.

## 6.3 Event

L'unità canonica è l'evento, non ogni pagina del relativo minisito.

Pagine come:

- home;
- conference topics;
- fees;
- sponsors;
- support;
- photo gallery;
- program;
- template;

devono essere collegate o assorbite nell'evento canonico, non diventare eventi distinti.

Identità:

- acronimo;
- edizione/anno;
- titolo;
- start/end;
- località;
- root URL del minisito.

Non fondere edizioni differenti.

Date:

- selezionare start/end dalla stessa fonte quando possibile;
- `end >= start`;
- durata plausibile;
- preferire date reali a `YYYY-01-01`;
- `YYYY-01-01` può restare solo con `date_precision=year`;
- rimuovere end date impossibili o provenienti da un altro evento;
- non correggere inventando date.

Descrizione:

- rimuovere cookie banner, menu, `Work in progress`, `Past Event` e navigazione;
- conservare introduzione, scopo, luogo e informazioni sostanziali;
- non importare intere pagine con menu, turismo, speaker list e footer quando esiste una descrizione sintetica;
- se la descrizione appartiene chiaramente a un altro anno, non usarla.

Asset:

- `logo` prioritario come visuale evento;
- PDF/programmi/template come `document`;
- non importare indiscriminatamente tutte le immagini del sito evento;
- evitare che una pagina secondaria generi una nuova entità.

## 6.4 Publication

Identità forte:

- DOI;
- titolo completo;
- anno;
- autori;
- PDF.

Riconoscere come frammenti e non come pubblicazioni:

- titoli solo numerici (`13`, `20`, `04`);
- dimensioni file (`1 MB`);
- identificatori di pagina (`Publications76C3`);
- una riga di abstract usata come titolo;
- sottotitoli isolati;
- nomi di file o pulsanti download.

Quando il frammento è associabile con certezza a una pubblicazione completa, assorbirlo. Se è ambiguo tra più pubblicazioni, scartarlo come frammento e segnalarlo, senza scegliere arbitrariamente.

Il titolo canonico deve provenire dal record con metadati bibliografici reali, non dalla variante più frequente.

Conservare:

- titolo completo;
- anno;
- autori;
- journal;
- DOI;
- abstract;
- PDF e link sorgente.

## 6.5 Research area

Merge quasi esclusivamente su titolo canonico esatto o alias configurato.

Non fondere automaticamente aree correlate ma diverse:

- `Photovoltaics`;
- `2D Crystals and Photovoltaics`;
- `Photovoltaics and Semiconductor Materials`.

## 6.6 Sponsor

Usare nome, dominio ufficiale e logo.

Unire apostrofi tipografici e varianti URL con slash finale. Distinguere sponsor diversi dello stesso gruppo solo se esistono evidenze reali.

---

# 7. Risoluzione dei campi

Non scegliere l'intero record vincitore. Risolvere ogni campo separatamente.

Per ogni campo produrre:

```json
{
  "field": "location",
  "selected": "Yerevan, Armenia",
  "source_id": 253,
  "alternatives": ["Excursion", ""],
  "rule": "specific_non_boilerplate_location",
  "conflict": true
}
```

Regole generali:

- stringa vuota non sovrascrive valore;
- valore valido non viene sostituito da placeholder;
- testo completo prevale su testo troncato;
- testo pulito prevale su menu/boilerplate;
- valori complementari multivalore vengono uniti;
- valori singoli incompatibili generano conflitto;
- non concatenare automaticamente due body diversi;
- link e asset vengono uniti come set ordinato;
- mantenere al massimo una cover/logo primaria e una risorsa documentale primaria;
- deduplicare prima di assegnare `sort_order`.

---

# 8. Cluster safety

Non usare una semplice connected component senza controlli: A simile a B e B simile a C non implica che A sia uguale a C.

Prima di finalizzare un cluster verificare:

- assenza di hard conflict tra ogni coppia;
- coerenza di anno/data;
- coerenza dei strong identifiers;
- diametro massimo delle similarità;
- nessuna fusione di edizioni differenti;
- nessun record ponte generico.

Implementare un controllo `cluster_is_safe()`.

Se il cluster non è transitivamente sicuro:

- spezzarlo;
- oppure spostarlo in revisione manuale.

---

# 9. Classificazione dei risultati

Ogni risultato deve avere uno stato:

- `exact_duplicate`;
- `high_confidence_alias`;
- `page_fragment_attached`;
- `content_fragment_attached`;
- `manual_review_required`;
- `hard_conflict`;
- `junk_technical_record`;
- `kept_separate`.

Non usare un generico `similar`.

---

# 10. UI Data Quality

Creare una pagina dedicata, semplice e compatta, non un singolo pulsante.

## Sezioni

### Analisi

- contatori per tipo;
- duplicati esatti;
- candidati semantici;
- frammenti;
- record tecnici;
- conflitti data;
- link/asset duplicati;
- asset mancanti;
- distribuzione confidence;
- ultima analisi e fingerprint DB.

### Suggerimenti

Tabella filtrabile con:

- entità;
- record sorgente;
- proposta canonica;
- confidence;
- evidenze;
- conflitti;
- preview;
- azione.

### Azioni bundle

- merge automatici sicuri;
- attach page fragments;
- remove technical junk;
- normalize URLs;
- deduplicate assets;
- fix slugs;
- resolve date placeholders;
- nessuna azione applicata senza preview.

### Dettaglio cluster

Mostrare affiancati:

- titoli;
- date;
- testi;
- link;
- asset;
- status;
- fonte;
- campo scelto;
- motivazione.

Evitare JSON grezzo come interfaccia principale. Il JSON può essere disponibile in un pannello tecnico espandibile.

---

# 11. Sicurezza

- tutte le decisioni e mutazioni in Python server-side;
- JavaScript soltanto view/controller;
- CSRF sulle azioni;
- autorizzazione admin;
- validazione degli ID;
- nessuna regola o espressione arbitraria eseguita dal client;
- query parametrizzate;
- transaction DB;
- limite dimensione batch;
- timeout;
- lock contro due merge run concorrenti;
- escaping UI;
- log senza dati sensibili;
- nessun contenuto del DB inserito in prompt esterni;
- LLM locale opzionale e disattivabile;
- nessun CDN, tracking o telemetria.

---

# 12. Configurazione YAML

Usare un file leggibile, ad esempio:

```yaml
data_quality:
  enabled: true
  auto_merge_threshold: 0.96
  review_threshold: 0.78
  max_cluster_size: 20
  dry_run_default: true
  require_backup: true

  similarity:
    title_weight: 0.35
    content_weight: 0.20
    date_weight: 0.15
    url_weight: 0.15
    asset_weight: 0.10
    metadata_weight: 0.05

  hard_conflicts:
    different_doi: true
    different_event_year: true
    incompatible_date_range: true
    cross_entity_type: true

  optional_semantic:
    enabled: false
    provider: local
    embeddings_model: ""
    llm_model: ""
    candidate_generation_only: true
```

Validare lo YAML all'avvio. Parametri invalidi devono fallire chiaramente, non essere ignorati.

---

# 13. Persistenza minima

Aggiungere tabelle semplici o equivalenti:

- `data_quality_runs`;
- `data_quality_candidates`;
- `data_quality_clusters`;
- `data_quality_decisions`;
- `merge_operations`;
- `merge_field_decisions`;
- `entity_redirects` o `merged_into`;
- `rollback_snapshots`.

Non duplicare l'intero dominio in nuove tabelle se non serve.

---

# 14. API

Esempi:

```text
POST /admin/data-quality/analyze
GET  /admin/data-quality/runs/{run_id}
GET  /admin/data-quality/candidates
GET  /admin/data-quality/clusters/{cluster_id}
POST /admin/data-quality/clusters/{cluster_id}/decision
POST /admin/data-quality/preview
POST /admin/data-quality/apply
POST /admin/data-quality/rollback/{merge_run_id}
GET  /admin/data-quality/export-audit/{run_id}
```

Le operazioni lunghe possono essere processate in job interni semplici, senza introdurre infrastruttura distribuita.

---

# 15. Verifica dopo l'applicazione

Dopo ogni bundle verificare obbligatoriamente:

- nessun riferimento orfano;
- nessuno slug duplicato per tipo;
- nessun DOI duplicato non giustificato;
- nessun asset duplicato nello stesso record;
- start/end validi;
- nessuna entità con titolo vuoto;
- nessuna perdita di link/documenti;
- numero sorgenti = canonici + merged + rejected;
- idempotenza di una seconda analisi;
- rollback testato.

Se una verifica fallisce, rollback automatico della transazione.

---

# 16. Test obbligatori

## Unit test

- normalizzazione nomi;
- inversione nome/cognome;
- slug;
- DOI;
- URL Aruba;
- asset fingerprint;
- date e precisione;
- score;
- hard conflicts;
- field resolution;
- cluster safety;
- junk classifier.

## Fixture reali anonimizzate o ridotte

Includere casi equivalenti a:

- membro presente tre volte;
- news con titolo corto e titolo descrittivo;
- due accordi simili ma anni diversi;
- evento canonico più pagine topics/fees/gallery;
- due edizioni della stessa conferenza;
- pubblicazione completa più titolo `13`;
- sottotitolo comune a due pubblicazioni differenti;
- PDF presente soltanto in una copia;
- descrizione enorme con menu e una descrizione sintetica alternativa;
- date placeholder e date precise;
- URL HTTP/HTTPS e slash finale.

## Integration test

- analyze;
- preview;
- apply;
- invariant validation;
- second run idempotente;
- rollback;
- export audit.

## Browser test

- filtri;
- cluster detail;
- selezione campo;
- preview bundle;
- conferma;
- error handling;
- responsive layout;
- nessuna azione applicata accidentalmente.

Non modificare i test per farli passare. Devono validare effetti reali nel DB.

---

# 17. Logging

Logging strutturato server-side:

- run_id;
- cluster_id;
- entity type;
- regola;
- score;
- hard conflict;
- durata;
- numero candidati;
- numero auto-merge;
- numero review;
- numero scarti;
- verifica finale.

Non loggare body completi, email o dati sensibili. In UI mostrare messaggi leggibili e un riferimento al run.

---

# 18. Acceptance criteria

Il lavoro è accettabile soltanto se:

1. la vecchia logica inefficace viene rimossa senza lasciare codice morto;
2. l'analisi è eseguita dalla webapp;
3. ogni merge è spiegabile;
4. i merge automatici rispettano hard rules;
5. i cluster non usano transitività cieca;
6. link, PDF, immagini e metadati complementari non vengono persi;
7. i record tecnici vengono distinti dai duplicati;
8. le date non vengono inventate;
9. esistono preview, audit e rollback;
10. una seconda esecuzione è idempotente;
11. tutti i test passano davvero;
12. il codice resta semplice, modulare e leggibile;
13. l'interfaccia è compatta, coerente e comprensibile;
14. nessuna logica di business critica è affidata al JavaScript;
15. l'applicazione non modifica il DB durante la sola fase di analisi.

---

# 19. Deliverable finale

Consegnare:

- codice aggiornato;
- migrazioni DB;
- configurazione YAML;
- test unit, integration e browser;
- fixture;
- documentazione;
- report con regole implementate;
- esempio di dry-run;
- esempio di audit;
- prova di idempotenza;
- prova di rollback;
- elenco del vecchio codice rimosso;
- nessun file legacy o duplicato.

Prima di concludere, eseguire l'intera suite, mostrare i comandi usati e riportare risultati reali.
