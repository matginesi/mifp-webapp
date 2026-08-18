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
- `review_status` indica solo stato editoriale: `draft`, `review`, `published`, `archived`, `quarantined`, `duplicate`.
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

## Export canonici ZIP e JSONL

Gli export della dashboard usano `format: "mifp-jsonl-v2"` e `format_version: 2`. ZIP e JSONL rappresentano lo stesso package logico e possono essere re-importati indistintamente.

Lo ZIP contiene `records.jsonl`, `manifest.json`, gli asset sotto `assets/` e, per lo scope completo, `state.json`; record, state e asset sono coperti da dimensioni/hash SHA-256 nel manifest.

Il JSONL self-contained serializza lo stesso contenuto in un singolo file: una riga manifest riservata `_mifp`, i record canonici, l'eventuale stato durevole e gli asset in blocchi Base64 chunked verificati con SHA-256.

Per i pacchetti canonici v2, `format_version`, hash dei record e metadati di integrità degli asset sono obbligatori. L'import accetta ancora i vecchi pacchetti `mifp-export` per migrazione, ma ogni nuovo export viene prodotto esclusivamente nel formato canonico v2. I path ZIP e asset vengono validati e gli upload JSON/JSONL, manifest e state hanno limiti di dimensione separati.

Nel formato JSONL self-contained, manifest e stato durevole sono righe riservate `_mifp` e i byte degli asset sono inclusi come blocchi Base64 chunked con dimensione e SHA-256 verificati. Il contenuto logico del round-trip è quindi equivalente allo ZIP; lo ZIP resta preferibile per grandi quantità di asset perché è più compatto ed efficiente. L'import mantiene compatibilità con i vecchi JSONL data-only e con i vecchi package ZIP `mifp-export`.

## Validazione

```bash
python tools/validate_import_data.py SCRAPERS/OUTPUTS
```

## Avvio locale

Prepara `MIFPAPP/DATABASE/mifp.db` con la pipeline dati locale, quindi avvia:

```bash
./mifp local
```
