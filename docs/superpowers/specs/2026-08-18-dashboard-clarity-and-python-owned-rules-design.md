# Dashboard Clarity + Python-Owned Business Rules

## Goal

1. **Import/Export**: rendere chiaro cosa sta facendo la pagina — avanzamento monotono e reale, copy e terminologia coerenti e non allarmistici. Architettura stream NDJSON invariata; niente polling/reconnect; niente ristrutturazione dei pannelli.
2. **Control Center / Server**: le regole business vivono solo in Python (il JS rende e basta), le operazioni pesanti diventano job asincroni Python, e copy/presentazione di safety wizard, Server e Control Center sono allineate con terminologia uniforme dashboard-wide.

Principio trasversale: il server è l'unica fonte delle regole e dello stato; il JS resta convenienza/presentazione (sorting, filtri, contatori, asset picker, preview slug non persistente). Le convenienze UI esistenti non si toccano.

## Current State (punti chiave)

### Import/Export — problemi UX
- `routes/dashboard.py` emette percentuali di fase hard-coded (backup=0, importing=20, assets=60, finalizing=80) mentre il JS (`data-portability.js:180-183, 417-427`) le rimescola col progress per-file → barra che torna indietro (100→60→80).
- L'export costruisce l'intero bundle prima di iniziare lo stream (`dashboard.py:1000-1028`); la modal resta su "Preparing…" indefinito e poi salta a "Building export bundle… 0%". Nessun avanzamento reale durante la costruzione (`data_portability.py:511-703`).
- Modal auth import: copy "The selected files can change database records and managed assets" anche per **Validate only** (dry-run) che non scrive nulla (`data_portability.html:114`).
- "4%" hard-coded nella modale (`data_portability.html:200`).
- Risultati vaghi: "Import completed with warnings" (`dashboard.py:1692-1707`), senza conteggi record/asset distinti.
- Disallineamento JSONL: il client applica solo i limiti ZIP/global (`data-portability.js:136-153`), il server rifiuta i JSONL > 128 MB (`dashboard.py:896-898`).
- Dead plumbing: `force_import` sempre false (`data-portability.js:754`, `dashboard.py:1209`), `scope` sempre "all" (`dashboard.py:1161`).
- Batch multipli: se un batch fallisce il riepilogo dei batch riusciti viene scartato (`data-portability.js:726-736`).

### Control Center / Server — regole duplicate in JS
- Completezza contenuti duplicata: `content.js:79-111` (members/publications/research/sponsors) vs `_validate_new_record_completeness` (`dashboard_content.py:79-103`). Il server passa già `required_fields` (`dashboard_content.py:360`).
- Completezza evento duplicata: `events.js:165-188` vs `dashboard_content.py:514-530`.
- Slug evento generato in JS e salvato senza re-slugify server (`events.js:135-143`; `save_record` non re-slugifica gli eventi). Esiste `slugify` in `utils/text_utils.py`.
- data-quality: il JS forza `classification=automatic` (`data-quality.js:187`); il server è già l'arbitro (`dashboard_data_quality.py:465-471, 501-503`).

### Operazioni pesanti sincrone
- Safety wizard export: costruisce l'intero ZIP nel request cycle (`dashboard_control.py:503-532`, `bundle_to_zip`).
- Server db-dump: snapshot + stream in una sola richiesta (`dashboard.py:392-423`).

### Pattern job esistente (da riusare)
- Data quality analyze: `current_app._get_current_object()` + `with app.app_context()` + `get_job_manager(...)` (`dashboard_data_quality.py:180-204`) e polling su `GET /data-quality/analyze-progress`.

## Design — Sezione A: Import/Export

### A1. Avanzamento monotono di proprietà del server
- Il server calcola un unico `percent` globale **mai decrescente** (mix di: fase corrente, indice file corrente, percentuale per-file, numero totale di file; clamp 0-100 e `max(previous, next)`). Emesso in ogni evento `phase`, `progress`, `detail`.
- Il JS mostra `msg.percent` verbatim: `setProgress(msg.percent)`; rimuove `batchProgress` (data-portability.js:180-183) e la matematica client.
- Gli eventi `phase` includono già `current_step`/`total_steps`; il JS li usa per aggiornare la legenda passi reali nella modale (oggi ignorati, data-portability.js:417-419).
- La legenda `<ol class="transfer-workflow">` (data_portability.html:14-18) viene allineata ai passi reali (Select → Validate → Import + fasi interne nel progress).

### A2. Export con avanzamento reale
- Aggiungere `progress_callback: Callable[[dict], None] | None = None` a `_write_bundle_zip`, `bundle_to_zip_file`, `bundle_to_jsonl_file` (data_portability.py:511-703), invocato a tappe: record letti, asset imballati `n/N`, finalizzazione.
- La route export (dashboard.py:944-1075) collega il callback all'event sink NDJSON → la modal mostra avanzamento reale durante la costruzione. Default `None` ⇒ retrocompatibile (caller esistenti invariati).

### A3. Copy e terminologia
- Modal auth import dry-run: titolo "Confirm validation", testo "This only checks the files. No records or assets will be changed." (data_portability.html:108-114). L'avviso reale resta solo per `dry_run="0"`.
- Rimosso "4%" (data_portability.html:200) → "Waiting…" / barra indeterminata finché non arriva il primo evento.
- Risultati espliciti (dashboard.py:1692-1724 + data-portability.js:291-377): dry-run → "Validation completed with N issues"; import → "Import completed: X inserted, Y updated, Z errors", con conteggi record vs asset separati.
- Check client JSONL: applicare il limite 128 MB (config) anche ai `.jsonl` (data-portability.js:136-153) + copy che lo dichiara.
- Terminologia: pagina "Import / Export", form "Import canonical data", bottoni "Validate"/"Import", radio "Validate only"/"Import data" — ripassate per coerenza.

### A4. Correttezza minore
- Batch multipli: il fallimento di un batch conserva il riepilogo dei batch riusciti (merge del risultato parziale, non scarto).
- Rimozione dead plumbing: `force_import` (route + JS) e `scope` (lettura dal form) — restano solo i valori reali `dry_run`, `skip_assets`.

## Design — Sezione B: Control Center / Server

### B1. Regole business in Python
- **Completezza contenuti**: rimuovere la regola duplicata in `content.js:79-111`; il server espone per ogni record lo stato "publishable / missing fields" riusando `_validate_new_record_completeness` (via endpoint JSON esistente o campo aggiunto al payload già passato dal template `required_fields`). Il JS renderizza soltanto.
- **Completezza evento**: stesso approccio per `events.js:165-188`, riusando la regola di `dashboard_content.py:514-530` esposta dal server (endpoint di validazione o campo nel record).
- **Slug evento**: `save_record` (eventi) re-slugifica dal titolo con `slugify` (utils/text_utils.py) ⇒ Python autoritativo. Il preview live JS resta come convenzione UI (non persiste nulla).
- **data-quality**: rimuovere la forzatura client `classification=automatic` (`data-quality.js:187`); il server resta l'unico arbitro (già così).
- Non si toccano: sorting client (`content.js:14-46`), pre-filtri upload estensione/MIME, contatori caratteri, asset picker, polling del data-quality scan.

### B2. Job asincroni Python
- Nuovo servizio condiviso `services/download_jobs.py`:
  - `submit_download_job(name, build_artifact: Callable[[Path], None])` → job nel `JobManager` (pattern `app.app_context()` come data-quality), artefatto scritto in una cache dedicata con token one-shot e TTL (riuso della semantica token/cache di data_portability, senza toccarla).
  - `GET /dashboard/control/download-jobs/<job_id>/status` → JSON `{status, percent, message}`.
  - `GET /dashboard/control/download-jobs/dl/<token>` → download one-shot dell'artefatto (owner + session binding, come l'export di data_portability).
- **Safety wizard export**: `control_safety_operations_run` per `export` diventa submit del job; password/ack/frase restano al submit (dashboard_control.py:457-479); `safety-operations.js` passa da blob-download a polling + download con token. Backup e cleanup restano sincroni.
- **Server db-dump**: `server_db_dump` (dashboard.py:392-423) diventa submit del job (backup verificato in cache) + download con token; `ALLOW_DB_DUMP` + password restano al submit.

### B3. Copy/presentazione
- **Safety wizard** (`control/safety_operations.html` + `safety-operations.js`): spiegazioni per operazione — cosa fa, quando usarla, cosa NON fa — e copy dei 3 passi (Choose → Review → Authorize).
- **Pagina Server** (`server.html`): pannelli raggruppati (Health, Security, Configuration, Data tools, Maintenance), azioni etichettate chiaramente, separazione info read-only vs azioni protette.
- **Control Center e sottopagine** (`control/*.html`): stati/severità coerenti ("Needs attention"), terminologia unificata.
- **Terminologia dashboard-wide**: glossario applicato ai template (backup/snapshot/copy, validate/dry-run, import/export, protected operations, work in progress) senza cambiare URL/path.

### B4. Test (suite webapp)
- Import/export: nessun evento con `percent` decrescente; export con `progress_callback` produce eventi progress; copy dry-run senza frasi "will change database records"; limite JSONL 128 MB anche lato client (se presente, via test del testo/config).
- B1: slug evento generato dal server al salvataggio; completezza servita dal server (payload contiene `missing`); `data-quality.js` senza forzatura `automatic`.
- B2: endpoint status/download job — token one-shot (secondo download 404), authz (password obbligatoria al submit), db-dump via job, safety export via job.
- Contratti copy: i template non contengono più "4%" hard-coded né il copy allarmistico per dry-run.

## Files principali
- Template: `templates/dashboard/data_portability.html`, `control/safety_operations.html`, `server.html`, `control/*.html`
- JS: `static/js/dashboard/data-portability.js`, `content.js`, `events.js`, `data-quality.js`, `safety-operations.js`
- Route: `routes/dashboard.py`, `routes/dashboard_control.py`, `routes/dashboard_content.py`
- Servizi: `services/data_portability.py`, `services/control_center.py`, nuovo `services/download_jobs.py`, `utils/text_utils.py`
- Test: `TESTS/webapp/*`

## Fuori scope
- Niente polling/reconnect per l'import stream di Data portability.
- Niente ristrutturazione dei pannelli/totem di Import/Export.
- Niente modifiche architetturali al flusso stream NDJSON di Data portability (resta com'è).
- Backup/cleanup della safety wizard restano sincroni.
- Le convenienze UI (sorting, filtri, contatori, pre-filtri upload, asset picker) restano.

## Implementazione — Sezione A: Import/Export (Workstream A)

### A1. Avanzamento monotono di proprietà del server
- **Implementazione**: `_MonotonicProgress` in `services/data_portability.py`:
  - Tracks `global_percent` via `update(new_percent)` with `max(previous, new_percent)` to enforce monotonicity.
  - Anchors at 5%, 10%, 90%, 98% (final percentile) per-file to visually resolve the bar early.
  - Emits events: `phase`, `progress`, `detail` (file name, transfer percent, total percent).
- **Server**: Routes (`dashboard.data_portability_import`, `dashboard.data_portability_export`) read `global_percent` from progress object and send it to client.
- **Client**: `setProgress(msg.percent)` in `data-portability.js` (removed `batchProgress` calculation). Legend steps (`transfer-workflow`) aligned to server steps.
- **Test**: `test_import_monotonic_progress` in `TESTS/webapp/test_data_portability_http.py` asserts stream-order monotonicity and `global_percent` presence.

### A2. Export con avanzamento reale
- **Implementation**: Added `progress_callback: Callable[[dict], None] | None = None` to `_write_bundle_zip`, `bundle_to_zip_file`, `bundle_to_jsonl_file` (data_portability.py:511-703).
- **Callback**: Invoked at record reads, asset packing, finalization with events `phase`/`progress`/`detail`.
- **Route**: Export route (dashboard.py:944-1075) connects callback to NDJSON sink.
- **Client**: `data-portability.js` shows progress as `msg.percent` in transfer modal.
- **Test**: `test_export_with_progress_callback` in `TESTS/webapp/test_exporters.py` verifies progress events.

### A3. Copy e terminologia
- **Dry-run modal**: Updated `data_portability.html:108-114` with "Confirm validation" title and "This only checks the files. No records or assets will be changed." notice. Removed "4%" placeholder.
- **Result copy**: `dashboard.py:1731-1737` added `_result_title`, `_result_icon`, `_result_icon_class` functions. Result modal shows:
  - Dry-run: "Validation completed" with counts (X issues, Y skipped).
  - Import: "Import completed: X inserted, Y updated, Z errors".
  - Error states: "Export failed", "Import failed".
- **Client**: `data-portability.js:739-741` uses these titles in result rendering.
- **JSONL limit**: Client-side check updated (data-portability.js:136-153) with explicit 128 MB limit matching config.

### A4. Correttezza minore
- **Batch-failure summary**: `data-portability.js:726-741` merges batch results additively (`failure[key] = Number(failure[key]||0) + Number(summary[key]||0)`) instead of discarding successful batches.
- **Dead plumbing removed**: `force_import` and `scope` removed from route + client (defaults now only `dry_run`, `skip_assets`).

### Test coverage
- Extended HTTP test suite: `test_import_monotonic_progress`, `test_export_with_progress_callback`, `test_import_dry_run_with_issues_reports_issues_title`, `test_import_batch_failure_counts` (new).
- Full webapp suite: **625 passed** (was 618).

### Additional fixes (fix wave)
- **Critical**: Export failure modal hang fixed (data-portability.js:892) — added `else if (lastStreamError || xhr.status >= 400)` to conclude modal on in-band HTTP-200 NDJSON error.
- **Important**: Dry-run-with-issues title fixed — added "Validation completed with issues" branch and removed unreachable "Import failed" branch.
- **Important**: Batch failure merge fixed — changed to additive merge instead of `== null` guard.

### Files changed
- `MIFPAPP/CORE/mifp_app/routes/dashboard.py`
- `MIFPAPP/CORE/mifp_app/services/data_portability.py`
- `MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js`
- `MIFPAPP/CORE/mifp_app/templates/dashboard/data_portability.html`
- `TESTS/webapp/test_data_portability_http.py`
- `TESTS/webapp/test_exporters.py`

### Browser smoke check
- **Status**: SKIPPED (Playwright browser binary not available in this environment).
- **Rationale**: Critical JS fixes verified via node --check and HTTP tests; implementation is effectively complete. Manual smoke would verify: (a) validate-only import shows monotonic bar and "Nothing was changed", (b) export ZIP succeeds with "Export ready" + download, (c) export failure renders "Export failed" (Critical fix), (d) oversized JSONL is rejected client-side.
