# Data Quality — Interfaccia 2-fasi + Risoluzione Permanente

## Obiettivo

Rendere il sistema data quality **semplice e robusto**: interfaccia chiara, falsi positivi ridotti all'osso, coppie risolte che non riappaiono mai più.

## Problemi Riscontrati

1. **Interfaccia a 3 fasi** (Analyze → Review → Apply) troppo complessa
2. **Whack-a-mole**: dopo ogni merge/import, le stesse coppie ricompaiono
3. **`same_asset_checksum`** trattato come EXACT duplicate (FIXATO)
4. **`_apply_merge`** non impostava il canonico a `published` (FIXATO)
5. **200+ findings** per analisi, impossibile gestirli manualmente
6. **Merge_exclusions** basato su fingerprint con ID → non persiste tra re-import

## Panoramica Architetturale

```
Stato attuale:              Stato futuro:
3 fasi:                     2 fasi:
  Analyze                     Analyze → mostra summary
  Review                        [Apply Auto-Fixes]
  Apply                        Lista findings ambigui
```

Il cuore è l'identificatore permanente basato sul contenuto (non sull'ID del record).

## Componenti

### 1. Tabella `resolved_pairs`

```sql
CREATE TABLE IF NOT EXISTS resolved_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    left_fingerprint TEXT NOT NULL,
    right_fingerprint TEXT NOT NULL,
    action TEXT NOT NULL,           -- 'merged', 'rejected', 'cleaned'
    finding_id INTEGER,
    bundle_id INTEGER,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, left_fingerprint, right_fingerprint)
);
```

### 2. `content_fingerprint()` in `normalizers.py`

```python
def content_fingerprint(record: dict) -> str:
    """Hash del contenuto SENZA id, slug, sort_order, created_at, updated_at."""
    exclude = {'id', 'slug', 'sort_order', 'source_order',
               'display_order', 'created_at', 'updated_at'}
    clean = {k: v for k, v in record.items() if k not in exclude}
    return hashlib.sha256(
        json.dumps(clean, sort_keys=True, default=str).encode()
    ).hexdigest()
```

### 3. Flusso `_pairwise_findings` in `analyzer.py`

Prima di emettere un finding:
1. Calcola `content_fingerprint` per entrambi i record
2. Ordina deterministicamente: `pair = sorted([fp_a, fp_b])`
3. Controlla `resolved_pairs`: se `(entity_type, pair[0], pair[1])` esiste → `continue`
4. Controlla `review_status`: se uno dei record è `duplicate` → `continue`

### 4. Scrittura `resolved_pairs` in `executor.py`

Dopo ogni azione applicata:
- `_apply_merge`: per ogni coppia (canonico, non-canonico), scrive `action='merged'`
- `_apply_clean`: per il record pulito, scrive `action='cleaned'`
- Reject/decision "keep_separate": scrive `action='rejected'`

### 5. Interfaccia a 2 fasi

#### Fase 1: Analisi
- Pulsante "Analyze Database" con progress bar
- Summary cards: auto-fixes pronti, findings da rivedere
- Filtri semplificati (solo entity_type, niente action_type/classification)

#### Fase 2: Applica
- Pulsante "Apply All Auto-Fixes" — applica TUTTI i fix automatici in transazione
- Findings ambigui mostrati in lista ridotta
- Ogni card mostra:
  - Titolo entità e tipo
  - Evidenza (perché è stato trovato)
  - Pulsanti [Merge] e [Ignore per sempre]
- "Ignore per sempre" → resolved_pairs + merge_exclusions

### 6. Compatibilità Database

- La tabella `resolved_pairs` viene creata con `CREATE TABLE IF NOT EXISTS`
- Nessuna modifica a tabelle esistenti
- I dati pregressi rimangono intatti

## Dipendenze

- `normalizers.py`: nuova funzione `content_fingerprint`
- `analyzer.py`: check `resolved_pairs` in `_pairwise_findings`
- `executor.py`: scrittura `resolved_pairs` dopo apply
- `dashboard_data_quality.py`: rotte semplificate
- `data_quality.html`: template a 2 fasi
- `data-quality.js`: frontend semplificato

## Test

- Test per `content_fingerprint()`: stesso contenuto → stesso hash, ID diversi → stesso hash
- Test per resolved_pairs check: coppia risolta non genera finding
- Test per auto-reject: record duplicate skippato
- Test per interfaccia: render semplificato funziona
