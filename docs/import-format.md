# MIFP JSONL v2 Import Format

MIFP usa un solo formato canonico per import/export manuale: JSONL v2.

Ogni riga e' un record indipendente:

```json
{"type":"event","data":{"title":"PLMCN 2026","start_date":"2026-06-01","end_date":"2026-06-05","location":"Lecce, Italy","description":"Conference description.","review_status":"published","is_featured":true},"links":[{"url":"https://example.org","role":"primary","label":"Website"}],"assets":[{"url":"https://example.org/poster.pdf","role":"document","caption":"Poster"}],"meta":{"source":"manual"}}
```

## Struttura

- `type`: uno tra `event`, `news`, `member`, `publication`, `research_area`, `page`, `sponsor`.
- `data`: oggetto con soli campi canonici del tipo scelto.
- `links`: opzionale, lista di link pubblici espliciti.
- `assets`: opzionale, lista di asset espliciti.
- `meta`: opzionale, solo audit/import info. Non diventa contenuto pubblico.

Il nome file non determina il tipo. Ogni riga deve avere `type`.

## Regole

- Niente formato legacy `{"table":"...","row":{...}}`.
- Niente oggetti piatti tipo `{"type":"event","title":"..."}`.
- Niente campi non canonici: l'import li rifiuta.
- `source_url` non diventa mai link pubblico. Mettilo in `meta.source_url` se serve audit.
- I link pubblici vanno solo in `links[]`.
- Gli asset vanno solo in `assets[]`.
- La temporalita' evento `forthcoming/past` e' derivata da `start_date`/`end_date`; non importare status temporali.
- `review_status` indica solo stato editoriale: `draft`, `review`, `published`, `quarantined`, `duplicate`. Il valore legacy `archived` non appartiene al contratto canonico ed è rifiutato dall’import runtime; la pipeline locale può normalizzarlo prima di produrre il package.
- Non usare `is_published`: la pubblicazione deriva da `review_status="published"`.

## Campi principali

Event:
`slug`, `title`, `start_date`, `end_date`, `date_text`, `date_precision`, `location`, `description`, `event_type`, `series_key`, `parent_event_id`, `review_status`, `is_featured`, `sort_order`.

News:
`slug`, `title`, `news_type`, `card_layout`, `date`, `date_text`, `date_precision`, `date_is_inferred`, `date_inference_rule`, `original_date_text`, `summary`, `body`, `review_status`, `is_featured`, `source_kind`, `source_priority`, `source_order`, `display_order`, `sort_order`.

Member:
`slug`, `first_name`, `last_name`, `display_name`, `affiliation`, `country`, `email`, `role`, `field`, `bio`, `review_status`, `is_active`, `sort_order`.

Publication:
`slug`, `title`, `year`, `authors`, `journal`, `doi`, `abstract`, `date_text`, `date_precision`, `review_status`, `sort_order`.

Research area:
`slug`, `title`, `summary`, `description`, `review_status`, `sort_order`.

Page:
`slug`, `title`, `type`, `summary`, `body`, `version`, `effective_date`, `nav_group`, `menu_order`, `review_status`, `sort_order`.

Sponsor:
`slug`, `name`, `description`, `sponsor_type`, `tier`, `is_active`, `sort_order`.

## Links e asset

Link:

```json
{"url":"https://example.org","role":"primary","label":"Website","is_primary":true,"sort_order":1}
```

Asset:

```json
{"url":"https://example.org/logo.png","role":"logo","alt_text":"Sponsor logo","is_primary":true}
```

Ruoli link consigliati: `primary`, `website`, `source`, `doi`, `publisher`, `registration`, `program`, `document`, `social`, `other`.

Ruoli asset consigliati: `cover`, `gallery`, `attachment`, `logo`, `document`, `profile`.

## Package ZIP e JSONL

MIFP usa due package ZIP distinti perché hanno scopi diversi. La pipeline locale degli scraper produce un **content package** `mifp-content`/`format_version: 1`: contiene `manifest.json`, `records.jsonl` e gli eventuali file sotto `assets/`, ma non include stato operativo della webapp. È il formato normale per portare nella dashboard i dati raccolti localmente.

La dashboard può invece esportare un **portable package** `mifp-jsonl-v2`/`format_version: 2`. Contiene gli stessi record canonici e gli asset e, nello scope completo, può aggiungere `state.json` per preservare lo stato durevole supportato dall'import/export applicativo. Non è un backup byte-per-byte di SQLite.

Prima di creare lo ZIP, l'export della dashboard prova anche a **materializzare temporaneamente gli asset remoti MIFP** tracciati nel database. Di default il dominio di preservazione è `mifp.eu`, quindi sono inclusi anche `www.mifp.eu`, `old.mifp.eu`, `events.mifp.eu` e gli altri sottodomini. I file vengono scaricati in staging e inseriti nello ZIP senza modificare il database o la libreria asset live. Gli URL di terze parti non vengono copiati automaticamente. Il manifest contiene un oggetto `preservation` con conteggi, eventuali errori e `remaining_remote`; gli ZIP v2 più vecchi senza questo oggetto restano validi.

Il re-import di soli ZIP dashboard `mifp-jsonl-v2` è volutamente **offline**: ripristina i file presenti nel package e non lancia la recovery HTTP post-import. Se un asset MIFP non è stato materializzato durante l'export, resta esplicitamente irrisolto invece di rendere il restore dipendente dal vecchio sito. JSONL e package scraper `mifp-content` mantengono invece il normale comportamento di ingest/recovery degli asset remoti.

In entrambi i package moderni `records.jsonl` è protetto da SHA-256 nel manifest; gli asset locali dichiarano percorso, dimensione e SHA-256. Il package portabile completo protegge anche `state.json`. Per un asset, `archive_path` contiene esattamente un prefisso `assets/`: se `path` lo contiene già non viene aggiunto di nuovo. Path ZIP, file inattesi, dimensioni, checksum e limiti di decompressione vengono validati prima dell'import.

L'export JSONL della dashboard è volutamente **record-only**: una riga JSON per record canonico, senza stato dell'installazione e senza asset binari in Base64. È il formato da usare per ispezione, pipeline e versionamento dei dati, non per un ripristino completo.

Non è mantenuta compatibilità con i vecchi ZIP `mifp-export`, con ZIP privi di `format`/`format_version` o con i vecchi JSONL self-contained `_mifp`: sono tutti rifiutati. Gli scraper emettono `mifp-content` v1 e gli export ZIP della dashboard emettono `mifp-jsonl-v2` v2.

## Validazione

```bash
python tools/validate_import_data.py SCRAPERS/OUTPUTS
```

## Avvio locale

Prepara `MIFPAPP/DATABASE/mifp.db` con la pipeline dati locale, quindi avvia:

```bash
./mifp local
```
