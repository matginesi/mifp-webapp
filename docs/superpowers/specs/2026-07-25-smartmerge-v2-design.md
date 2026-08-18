# SmartMerging v2 — Merge groups con diff e override

## Obiettivo

Ricostruire SmartMerging da zero con matching a gruppi (non coppie), merge diretto senza bundle, diff interattivo con override campo-per-campo, e SSE progress bars. Risolvere i problemi della v1: falsi positivi (soprattutto news), flusso macchinoso, merge che "non faceva nulla".

## Architettura

```
analyze → groups → select → merge (SSE progress)
   ↕                   ↑
  auto-detect      select all/safe/probable
  multi-campo       + expand diff + override
```

- **Analyzer**: read-only, genera gruppi di 2+ record simili, produce field_plan con best value per campo
- **Merger**: prende gruppi approvati, backup DB, transazione, merge con rollback
- **No bundles**: merge diretto, niente dry-run, niente bundle actions
- **CSS**: solo classi nuove `.sm2-*`, minimo impatto su dashboard.css

## Modello dati

Nuova tabella `smart_merge_groups` (sostituisce `smart_merge_candidates`):

```sql
CREATE TABLE smart_merge_groups(
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES smart_merge_runs(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    record_ids TEXT NOT NULL,       -- JSON array [1, 2, 5]
    record_key TEXT NOT NULL UNIQUE,-- "member:1,2,5"
    score REAL NOT NULL DEFAULT 0,
    classification TEXT NOT NULL DEFAULT 'possible', -- safe/probable/possible
    title TEXT,
    canonical_id INTEGER,
    field_plan TEXT,                -- JSON: [{"field":"title","values":{"1":"X","2":"Y"},"best":2,"action":"keep"}]
    conflicts TEXT,                 -- JSON array
    decision_state TEXT DEFAULT 'pending',
    decision_note TEXT,
    fingerprint TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

`smart_merge_candidates`, `smart_merge_bundles` tables rimosse (con migrazione).

## Service layer

### `analyzer.py` — Generazione gruppi

**Fase 1 — Blocking**: chiavi normalizzate per entity type:

| Entity | Blocking keys |
|--------|---------------|
| member | email_normalizzata, cognome+iniziale, URL affiliato |
| event  | series_key, data+luogo, titolo_stemmed |
| news   | content_hash(body), URL_source, titolo_deprefixed |
| publication | DOI, titolo_normalizzato |

**Fase 2 — Pair scoring**: per ogni coppia in un blocco, calcola score multi-campo:

```
member_score = w1 * email_match + w2 * name_fuzzy + w3 * affil_fuzzy
event_score  = w1 * title_fuzzy + w2 * date_compat + w3 * location_fuzzy + w4 * series_match
news_score   = w1 * title_fuzzy + w2 * date_compat + w3 * body_overlap + w4 * source_match
```

Pesi:
- email_match/DOI_match/URL: esatto → 1.0, alias Gmail → 0.9
- title_fuzzy: Jaccard su trigrammi (0-1)
- date_compat: date identiche → 1.0, sovrapposte → 0.8, vicine → 0.5
- body_overlap (news): cosine similarity TF-IDF su summary+body (0-1)
- source_match (news): stesso dominio URL → 0.8
- affiliation/venue: fuzzy dopo normalizzazione (0-1)

Threshold: score ≥ 0.4 → candidato, < 0.4 → scartato.

Classificazione:
- ≥ 0.9 → safe (match forte su 2+ campi)
- ≥ 0.7 → probable
- ≥ 0.4 → possible

**Fase 3 — Clustering**: single-linkage sulle coppie:
- Se A↔B è candidato E B↔C è candidato → {A, B, C} gruppo
- Eredita score minimo del gruppo, classification peggiore

**Field plan**: per ogni campo del gruppo:

Formato:
```json
{
  "field": "title",
  "values": {"1": "Mario Rossi", "34": "Mario R.", "87": "M. Rossi"},
  "best_record_id": 1,
  "best_value": "Mario Rossi",
  "chosen_record_id": 1,
  "action": "keep",
  "requires_review": false,
  "reason": "Longest non-placeholder value"
}
```

- Raccogli tutti i valori dai record coinvolti
- Scarta placeholder (None, "", "TBD", "N/A", "Untitled", date-placeholder "1970-01-01")
- Elegge "best": valore più lungo, con preferenza record attivi, non-placeholder
- Conflitto: 2+ valori validi ma diversi → `requires_review=True`
- `action`: "keep" (usa best), "override" (utente override), "skip" (placeholder)

### `merger.py` — Esecuzione merge

Funzione `merge_groups(db_path, assets_dir, group_ids, progress_callback=None)`:

1. Legge gruppi da DB, valida esistenza record
2. Backup DB via `backup_sqlite_database()`
3. BEGIN IMMEDIATE
4. Per ogni gruppo:
   - Per ogni campo in field_plan: aggiorna canonical record col valore scelto
   - Sposta link/asset/relations dagli absorbed al canonical
   - DELETE absorbed records
5. Integrity check
6. COMMIT
7. On error: ROLLBACK + quarantine rollback

### `normalization.py` — Helper di normalizzazione (invariato dalla v1)

## Dettaglio merge operazioni

Per ogni gruppo con canonical_id C e absorbed_ids [A, B, ...]:

1. **Field merge**: UPDATE tabella SET field1=valore_scelto, field2=... WHERE id=C
   - Usa `_apply_field_plan()` dalla v1, adattato per field_plan multi-record
2. **Link transfer** (entity_links): UPDATE entity_id=C WHERE entity_id IN (A,B,...) AND entity_type=X
3. **Asset link transfer** (asset_links): UPDATE entity_id=C WHERE entity_id IN (A,B,...)
4. **Relation transfer** (entity_relations): UPDATE source_id=C WHERE source_id IN (A,...), same for target_id
5. **DELETE** absorbed records
6. **Normalize primary flags** su asset_links/entity_links

## Route

| Metodo | Path | Handler | Descrizione |
|--------|------|---------|-------------|
| GET | `/dashboard/data-quality` | `data_quality_page` | Dashboard page render (sostituisce `/smart-merge`) |
| POST | `/dashboard/data-quality/analyze` | `data_quality_analyze` | Avvia analisi, restituisce run_id |
| GET | `/dashboard/data-quality/groups` | `data_quality_groups` | Lista gruppi (filtrata/paginata) |
| GET | `/dashboard/data-quality/groups/<id>` | `data_quality_group` | Dettaglio gruppo con field_plan |
| POST | `/dashboard/data-quality/groups/<id>/decision` | `data_quality_group_decision` | Approva/scarta/skip |
| GET | `/dashboard/data-quality/merge-stream` | `data_quality_merge_stream` | SSE merge progress |
| POST | `/dashboard/data-quality/merge` | `data_quality_merge` | Fallback JSON |

Tutte con `@login_required`. La vecchia route `/smart-merge` redirect a `/data-quality`.

## UI

### Template: `dashboard/data_quality.html`

2 tab:
- **Analysis**: KPI (gruppi totali, per tipo, per classificazione), "Analyze" button, "Merge all safe"
- **Merge groups**: selection toolbar + tabella responsive espandibile

### JS: `dashboard/data-quality.js`

- Nessuna logica business: solo chiamate AJAX/SSE al backend
- `startMergeStream(url)` → EventSource, progress modal
- Selection toolbar (select all/safe/probable/none)
- Expand inline con diff table
- Decision buttons (approve/keep_separate/later)

### CSS

Classi nuove `.dq-*` in dashboard.css (~30 loc). Nessuna modifica a classi esistenti.

## Sicurezza

- `@login_required` su tutte le route
- CSRF validation su POST
- Input validation: group_ids devono esistere, run_id match
- Transaction rollback su qualsiasi errore di merge
- Rate limiting sulle POST
- Backup DB prima di ogni merge

## Test

### `test_data_quality.py` (nuovo file)

- `test_analysis_detects_duplicate_members_by_email` — due membri stessa email → gruppo safe
- `test_analysis_detects_duplicate_events_by_series_and_date` — stesso series_key + stessa data → gruppo
- `test_analysis_clusters_three_identical_news` — tre news uguali → gruppo di 3
- `test_analysis_rejects_unrelated_records` — record diversi → nessun gruppo
- `test_merge_safe_group_works` — analisi → merge gruppo safe → verifica unione
- `test_merge_preserves_canonical_data` — merge con field_plan → canonical ha i best values
- `test_merge_rollback_on_error` — forza errore → rollback + dati intatti
- `test_keep_separate_persisted_across_runs` — decisione persiste via fingerprint
- `test_scale_guard` — 1000+ record con blocking evita combinatorial explosion

## Ordine implementazione

1. Database migration: nuova tabella `smart_merge_groups`, droppa `smart_merge_candidates`/`smart_merge_bundles`
2. `services/smart_merge/` — normalization (existente, da ripristinare), analyzer, merger, repository
3. Route in `dashboard.py` — tutte le route /data-quality/*
4. Template `data_quality.html` — due tab, tabella espandibile, progress modal
5. CSS in `dashboard.css` — classi `.dq-*`
6. JS `data-quality.js` — chiamate backend, SSE, selection toolbar
7. Test `test_data_quality.py`
8. Collega data_portability.html al nuovo endpoint
9. Redirect vecchia route `/smart-merge` → `/data-quality`
