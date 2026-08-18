# Import Totem Layout Redesign

## Goal
Riorganizzare la modale di import (totem) in un layout a due pannelli con progress bar per-file visive, metriche live, e log più leggibile.

## Current State
La working area della modale `#transferModal` ha un layout lineare:
1. Spinner + titolo + detail
2. Barra progresso principale
3. Step indicator (5 fasi testuali)
4. File progress (nome + percentuale in testo)
5. Live log (box scrollabile piccolo)

Problemi:
- Le percentuali per-file sono solo testo, no barra visuale
- Il log è piccolo e nascosto in fondo
- Manca un riepilogo live (record processati, asset scaricati, errori)
- La barra principale ha range confondente (upload 0-45%, import 45-95%)

## Design

### Layout a due pannelli
La modale `modal-lg` viene riorganizzata in una griglia a due colonne:

```
┌──────────────────────────────────────────────────────┐
│ Modal header: "Import data"                          │
├────────────────────────┬─────────────────────────────┤
│ LEFT PANEL (.45fr)     │ RIGHT PANEL (.55fr)         │
│                        │                             │
│ Phase icon + title     │ Per-file progress bars:     │
│ Detail text            │ ┌─ events.jsonl ──────────┐ │
│                        │ │ ████████████░░░░  55%   │ │
│ Main progress bar      │ └────────────────────────┘ │
│ ████████████████░░ 67% │ ┌─ members.jsonl ────────┐ │
│                        │ │ ██████░░░░░░░░  30%    │ │
│ Live metrics:          │ └────────────────────────┘ │
│ Records:  120 / 360    │ ┌─ news.jsonl ───────────┐ │
│ Assets:   45           │ │ ░░░░░░░░░░░░░░   0%    │ │
│ Errors:   0            │ └────────────────────────┘ │
│                        │                             │
│ (step indicator gone)  │ Live activity (3 lines):    │
│                        │ > Importing events.jsonl    │
│                        │ > Downloading asset.jpg     │
│                        │ > Record 45/360 processed   │
├────────────────────────┴─────────────────────────────┤
│ Modal footer: [Close]                                │
└──────────────────────────────────────────────────────┘
```

### Componenti

#### Left Panel
- **Phase header**: icona (spinner/check) + titolo fase correnti (es. "Importing records…") + detail text sotto
- **Main progress bar**: barra singola 0-100%, percentuale e tempo trascorso sotto
- **Live metrics**: tre righe dati live inviate dal server via evento `metrics`

#### Right Panel
- **Per-file progress bars**: ogni file ha una riga con nome + barra visuale + percentuale. Grigia in attesa, blu durante, verde a completamento.
- **Live activity log**: 3-4 righe di log non scrollabili, mostrano le ultime attività. Ogni riga ha icona (→ per progresso, ✓ per completato, ✗ per errore).

### Nuovo evento `metrics`
Il server invia periodicamente un evento aggiuntivo durante l'import:

```json
{"event": "metrics", "records": 45, "total_records": 360, "assets_linked": 12, "errors": 0, "asset_errors": 0}
```

Viene emesso dopo ogni record elaborato (o ogni N record su file grandi).

### Modifiche JS
- `file_start`: crea riga con barra visuale `<div class="file-bar-bg"><span class="file-bar-fill"></span></div>` + nome + etichetta percentuale
- `progress`: aggiorna larghezza `.file-bar-fill` e testo percentuale; aggiorna anche `setProgress` (ma la barra ora va 0-100 dritta, senza divisore upload/import)
- `metrics`: aggiorna le tre righe metriche nel pannello sinistro
- `detail`: aggiunge riga al live activity log (troncato a 4)
- `phase`: aggiorna icona + titolo nel pannello sinistro (elimina step indicator)

### Modifiche Server
- `_perform_import`: dopo ogni progress chiamata, emette anche evento `metrics` con i running totals
- Aggregazione: tiene contatori cumulativi di `inserted`, `updated`, `errors`, `linked_assets` durante l'import

### CSS
- Griglia `transfer-working-grid` con `grid-template-columns: .45fr .55fr` e gap
- `.file-bar-bg`: height 6px, bg grigio, bordo arrotondato, overflow hidden
- `.file-bar-fill`: transition width, accent color per active, green per done
- `.transfer-metrics`: tre righe con label + valore bold
- `.transfer-activity`: container per 3-4 log lines, non scrollabile

### Result area (invariata)
La schermata `#transferResult` rimane com'è (result mark + metrics pills + by-type grid + errori + merge).

## Files da modificare
- `templates/dashboard/data_portability.html` — struttura modale
- `static/css/dashboard.css` — nuovo layout due pannelli, file bars, metrics
- `static/js/dashboard.js` — nuovi handler eventi, bar rendering
- `routes/dashboard.py` — evento `metrics`
