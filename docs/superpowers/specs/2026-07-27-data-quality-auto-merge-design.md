# Data Quality — Riduzione intervento manuale

## Obiettivo
Ridurre al minimo i 209 errori di batch-accept rendendo automatiche più decisioni oggi gestite manualmente, senza compromettere la correttezza dei dati.

## Cambiamenti

### 1. Forza modalità `force=true` nel batch-accept
- JS `acceptAll()` chiama `acceptAll(true)` invece di `acceptAll()` — attiva `_force_candidate()` che converte AMBIGUOUS sopra soglia in auto-accettabili
- Nessun nuovo parametro: il route e `batch_add_to_bundle` supportano già `force`

### 2. Soglie `_force_candidate()` abbassate
| Entity | Prima | Dopo |
|--------|-------|------|
| news | .78 | .65 |
| event | .84 | .70 |
| publication | .86 | .72 |
| member | .9 | .75 |
| sponsor | .9 | .75 |

### 3. Policies più aggressive — più STRONG, meno AMBIGUOUS

**members (`evaluate_member`):**
- Nome corrisponde + almeno un'affiliazione presente (anche una sola) → STRONG invece di AMBIGUOUS
- Rimuovere il requisito `.45` di similarità affiliazione per il caso STRONG

**events (`evaluate_event`):**
- Soglia STRONG: .90 → .82
- URL match senza year check → EXACT anche se years diversi (se stesso URL = stesso evento)

**news (`evaluate_news`):**
- Soglia body_score STRONG: .96 → .88
- Soglia title_score STRONG: .86 → .78

**publications (`evaluate_publication`):**
- Soglia STRONG title+author: .94 → .88
- Se author_score ≥ .65 e title_score ≥ .82 → AMBIGUOUS invece di RELATED

**sponsors (`evaluate_sponsor`):**
- Soglia STRONG: .94 → .88
- Soglia AMBIGUOUS: .82 → .75

### 4. Auto-gestione split_aggregated_record
`batch_add_to_bundle()` non salta più gli split: auto-deriva titoli dai segmenti e li accetta

### 5. `apply_best_quality()` risolve campi `manual_edit_required`
- Dopo la selezione best_quality, se ci sono ancora campi `requires_review: true` con `action: manual_edit_required`, prova a risolverli:
  - Se un campo ha `values_by_record` con esattamente un valore non-nullo, usa quello
  - Se tutti i valori sono null, imposta `action: review` ma non blocca l'accettazione

### 6. Dettagli errore visibili al frontend
- `batch_add_to_bundle()` già restituisce `error_details` (max 10)
- JS `acceptAll()` mostra `error_details` in console.error + toast separati

## Non toccato
- `_has_effective_change()` — rimane invariato (punto 5 eliminato)
- Sistema alias/slug — rimane invariato
- Flusso apply (dry-run → backup → apply) — invariato
- Validazione bundle — invariata

## Files modificati
- `mifp_app/static/js/dashboard/data-quality.js` — force=true, error_details display
- `mifp_app/services/data_quality/executor.py` — soglie, split auto, apply_best_quality migliorato
- `mifp_app/services/data_quality/policies.py` — soglie più basse per STRONG
- `mifp_app/templates/dashboard/data_quality.html` — versione cache bump
