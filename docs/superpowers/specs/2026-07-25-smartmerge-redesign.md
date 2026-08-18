# SmartMerging Redesign — Merge diretto con progress SSE

## Obiettivo

Sostituire l'attuale flusso a 5 passaggi (analyze → decide → create bundle → dry-run → apply) con un flusso semplificato a 3 passaggi (analyze → select → merge) con progress bar in tempo reale tramite SSE. Rimuovere completamente il concetto di "bundle" dall'interfaccia utente e dal service layer.

## Cosa cambia

### Rimosso
- `services/smart_merge/planner.py` (intero file — creazione/validazione bundle)
- `models.BundleStatus` enum
- `repository.py` funzioni bundle: `create_bundle()`, `list_bundles()`, `get_bundle()`, `update_bundle_status()`, `bundle_operations()`
- 5 route bundle: `POST /bundles`, `GET /bundles/<id>`, `POST /bundles/<id>/dry-run`, `POST /bundles/<id>/apply`
- Template: tab "Bundle actions" rimosso da `smart_merge.html`
- JS: tutte le funzioni bundle in `smart-merge.js`
- CSS: classi `.sm-bundle-*`

### Semplificato
- `executor.py`: nuova funzione `merge_candidates(conn, assets_dir, candidate_ids, progress_callback=None)` che sostituisce `apply_bundle()`. Stessa logica interna ma senza bundle wrapper:
  1. Valida candidati (stale check, conflict check)
  2. Backup DB
  3. BEGIN IMMEDIATE
  4. Per ogni candidato: `_merge_entity_operation()` / `_merge_asset_operation()`, chiama `progress_callback(current, total, id, status, title)`
  5. Integrity check → COMMIT
  6. On error: ROLLBACK + quarantine rollback

### Invariato
- `analyzer.py`, `normalization.py`, `models.py` (solo rimosso BundleStatus)
- `repository.py` funzioni run/candidate/decision
- Tabella `smart_merge_bundles` lasciata in DB per dati storici

## Route

### Rimosse
| Metodo | Path | Handler |
|--------|------|---------|
| POST | `/dashboard/smart-merge/bundles` | `smart_merge_bundles_create` |
| GET | `/dashboard/smart-merge/bundles/<id>` | `smart_merge_bundle` |
| POST | `/dashboard/smart-merge/bundles/<id>/dry-run` | `smart_merge_bundle_dry_run` |
| POST | `/dashboard/smart-merge/bundles/<id>/apply` | `smart_merge_bundle_apply` |

### Nuove
| Metodo | Path | Handler | Descrizione |
|--------|------|---------|-------------|
| GET | `/dashboard/smart-merge/merge-stream` | `smart_merge_merge_stream` | SSE endpoint. Query params: `mode=safe&run_id=X` oppure `candidate_ids=1,2,3&run_id=X`. Streaming progress eventi. |
| POST | `/dashboard/smart-merge/merge` | `smart_merge_merge` | Fallback JSON. Body: `{candidate_ids: [], run_id, mode}`. Attende completamento, restituisce risultato. |

### Invariate (con @login_required)
- `GET /dashboard/smart-merge` — page render
- `POST /dashboard/smart-merge/analyze` — analisi database (esistente, risposta JSON)
- `GET /dashboard/smart-merge/runs/<id>` — dettaglio run
- `GET /dashboard/smart-merge/candidates` — lista candidati (filtrata/paginata)
- `GET /dashboard/smart-merge/candidates/<id>` — dettaglio candidato
- `POST /dashboard/smart-merge/candidates/<id>/decision` — salva decisione

### Nuove (SSE)
- `GET /dashboard/smart-merge/analyze-stream` — SSE endpoint per analisi con progress streaming (stessa logica di analyze ma con eventi `progress` intermedi per fasi: blocking, comparing, assets, saving). Il frontend usa questo invece del POST quando disponibile.

### Sicurezza
- `@login_required` su tutte le nuove route
- Validazione input: candidate_ids devono esistere e appartenere al run_id fornito
- Idempotenza via fingerprint check (impedisce doppio merge dello stesso record)
- Lock concorrenza: flag in sessione/blocco atomico previene merge simultanei
- Gestione errori: transazione rollback + quarantine rollback su fallimento

## SSE (Server-Sent Events)

Endpoint: `GET /smart-merge/merge-stream`

Eventi emessi:
```
event: progress
data: {"current":1,"total":10,"candidate_id":5,"title":"Mario Rossi ↔ Mario R","status":"merging"}

event: progress
data: {"current":1,"total":10,"candidate_id":5,"title":"Mario Rossi ↔ Mario R","status":"merged"}

event: progress
data: {"current":2,"total":10,"candidate_id":7,"title":"Event X ↔ Event Y","status":"skipped","reason":"Stale — data changed since analysis"}

event: complete
data: {"ok":true,"merged":9,"skipped":1,"backup":"pre-merge-2025-07-25--14-30-00.db","results":[...]}

event: error
data: {"ok":false,"message":"Database error during merge"}
```

Il client EventSource:
1. Apre connessione → mostra progress modal overlay
2. Ogni `progress`: aggiorna barra + log
3. `complete`: mostra risultato, ricarica pagina
4. `error`: mostra errore, abilita retry
5. Riconnessione automatica (browser nativo EventSource) con backoff

## Frontend

### smart_merge.html (template)

Da 3 tab a **2 tab**:

**Tab 1 — Analysis**:
- Stessi KPI (records, safe, probable, blocked, asset duplicates, unused, missing)
- Entity metrics table, database health, instrumentation
- "Analyze database" button (con progress bar durante analisi tramite polling o SSE)
- **Bottone "Merge all safe (N)"** in evidenza quando ci sono safe candidati
- Bottone "Review merge candidates" → passa al tab 2

**Tab 2 — Merge candidates**:
- Filtri (entity type, classification, decision, conflicts, sort, search)
- **Selection toolbar**: "Select all" | "Select safe" | "Select probable" | "Deselect all" + contatore
  - "Select all" opera sulla lista **visibile** (rispetta i filtri attivi)
  - "Select safe" seleziona solo i candidati con classification=safe nella pagina corrente
- Lista candidati con checkbox, classificazione, titolo, ragione
- Click su riga → expand inline mostra dettaglio (evidence, field plan, bottoni decide)
- **Action bar**: "Merge selected (N)" + "Merge all safe (N)"

Progress modal overlay durante merge:

```
┌─────────────────────────────────────┐
│  Merging candidates...              │
│  ┌─────────────────────────────────┐│
│  │ ████████████░░░░░░ 5/12         ││
│  └─────────────────────────────────┘│
│  ✓ Merged: Mario Rossi ↔ Mario R  │
│  ⟳ Merging: Event X ↔ Event Y     │
│  ○ Pending: News A ↔ News B       │
│  ✗ Skipped: News C (stale)        │
│                                     │
│  [View results]   [Close]          │
└─────────────────────────────────────┘
```

### smart-merge.js

Riscrittura (~434 → ~350 loc):

**Rimosso**:
- `state.activeBundle`
- Funzioni: `createBundle()`, `addBundleRow()`, `openBundle()`, `renderBundleDetail()`, `dryRun()`, `applyBundle()`
- Logica bundle in `routeFromHash()` e `renderRun()`

**Nuovo**:
- `state.mergeActive` (flag merge in corso)
- `startMerge(mode, candidateIds)` — apre EventSource, overlay progress, gestisce eventi
- `renderProgress(event)` — aggiorna barra, log scroller
- `openSelectionToolbar()` — selectAll/deselectAll/selectSafe/selectProbable
- `renderCandidateInline(item)` — expand/collapse nella riga

**Modificato**:
- `renderCandidates()` — stile riga espandibile invece di side panel
- `updateSelection()` — aggiorna contatore + bottoni merge
- `renderRun()` — nasconde bundle info, mostra "Merge all safe" button
- `routeFromHash()` — solo analysis/merge tabs

### CSS

- **Rimosse** classi bundle: `.sm-bundle-list`, `.sm-bundle-row`, `.sm-bundle-summary`, `.sm-apply-confirm`, `.sm-inline-warning`, `.sm-success-note`, `.sm-bundle-layout`
- **Nuove**: `.sm-progress-overlay` (overlay fisso), `.sm-progress-card` (card centrata), `.sm-progress-log` (lista operazioni), `.sm-progress-line` (voce log), `.sm-selection-toolbar` (bottoni selezione massa), `.sm-action-bar` (bottoni merge), `.sm-candidate-expand` (dettaglio inline)

## Testing

### Rimosso da `test_smart_merge.py`
- `test_bundle_is_atomic_backed_up_and_idempotent`
- `test_stale_bundle_is_blocked_without_partial_write`

### Nuovi
- `test_merge_safe_candidates_works` — analisi, merge safe candidates, verifica merge
- `test_merge_selected_candidates_works` — approva candidato, merge selezionato, verifica
- `test_merge_skips_stale_candidates` — modifica dati tra analisi e merge, stale viene saltato
- `test_merge_rollback_on_error` — forza errore, verifica rollback + dati intatti

### Mantenuti
- Normalizzazione, analisi, decisioni persistite, scale guard

## Ordine di Implementazione

1. Service layer: `executor.py` — aggiungi `merge_candidates()`, rimuovi bundle logic. `planner.py` — elimina.
2. Repository: rimuovi funzioni bundle, aggiorna import.
3. Route: aggiungi `/smart-merge/merge-stream` e `/smart-merge/merge`, rimuovi bundle routes.
4. Template: riscrivi `smart_merge.html` (2 tab, selection toolbar, progress modal).
5. CSS: rimuovi `.sm-bundle-*`, aggiungi nuove classi.
6. JS: riscrivi `smart-merge.js` (SSE, batch selection, merge flow).
7. Test: aggiorna `test_smart_merge.py`.
8. Full test suite: verifica 0 regressioni.
