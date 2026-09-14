# Database MIFP: contratto e lifecycle

SQLite è la fonte di verità runtime. Lo schema corrente è **v9**.

- `mifp_app/db/schema.sql`: struttura completa di un DB nuovo.
- `mifp_app/db/contract.py`: tabelle/colonne/indici/trigger indispensabili.
- `mifp_app/db/migrations.py`: solo migrazioni forward esplicite tra versioni
  supportate; non contiene riparazioni storiche generiche.
- `mifp_app/db/runtime_check.py`: verifica fail-closed del DB prima dell'avvio.

## Regole

1. Un DB nuovo deve essere equivalente a un DB portato alla versione corrente.
2. Il runtime non crea e non migra schema.
3. Un DB incompleto non viene autoriparato: viene rifiutato.
4. La compatibilità storica dei dati passa dai vecchi ZIP importabili, non da
   vecchi layout SQLite o vecchi JSONL.
5. Upgrade schema: sempre su una copia, validazione, poi swap esplicito.
6. Import contenuti: transazionale e additivo; nessuna cancellazione implicita.
7. Backup fisico e export dati sono concetti diversi.
8. Il codebase è immutabile a runtime: le modifiche editoriali persistono nel DB; i Markdown inclusi nell'immagine sono solo fallback iniziali.

## Tabelle

### Contenuti

| Tabella | Responsabilità |
|---|---|
| `roles` | ruoli dei membri |
| `members` | persone/membri |
| `events` | eventi e relazioni parent/child |
| `news` | news/annunci |
| `publications` | pubblicazioni |
| `research_areas` | aree di ricerca |
| `pages` | contenuti pagina importati/canonici e pagine istituzionali modificabili dalla dashboard |
| `sponsors` | sponsor |

### Asset e relazioni

| Tabella | Responsabilità |
|---|---|
| `assets` | identità e metadata dei file/URL |
| `asset_recovery_state` | tentativi di recupero asset remoti |
| `asset_links` | asset -> entità |
| `entity_links` | URL/link esterni -> entità |
| `entity_relations` | relazioni polimorfiche tra entità |

Le relazioni polimorfiche non possono avere FK SQL verso tabelle diverse; delete
e merge devono quindi passare da `services/entity_references.py`.

### Provenance scraper

| Tabella | Responsabilità |
|---|---|
| `source_systems` | sorgenti originali |
| `source_runs` | esecuzioni scraper/parser |
| `source_records` | record/raw payload provenienti dalla sorgente |
| `canonical_mappings` | mapping provenance -> UID canonico |

Queste tabelle descrivono **da dove viene il dato**.

### Import e operazioni runtime

| Tabella | Responsabilità |
|---|---|
| `import_runs` | audit di un'importazione nell'app |
| `import_records` | record toccati da quell'import |
| `join_requests` | richieste di adesione |
| `metrics_daily` | metriche aggregate privacy-safe |
| `settings` | impostazioni persistenti allowlisted |

`import_*` non duplica `source_*`: descrive **cosa ha fatto l'app durante
l'import**, non l'origine dello scraping.

### Data Quality

| Tabella | Responsabilità |
|---|---|
| `quality_runs` | esecuzioni del motore Data Quality |
| `quality_findings` | finding/azioni proposte |
| `merge_exclusions` | coppie/record da non fondere |
| `quality_bundles` | bundle di revisione/applicazione |
| `quality_bundle_items` | elementi dei bundle |
| `content_aliases` | vecchio slug -> entità canonica |
| `resolved_pairs` | decisioni già applicate su coppie |

### Conferenze

| Tabella | Responsabilità |
|---|---|
| `conference_sites` | configurazione micrositi conferenza |
| `conference_people` | persone del microsito |
| `conference_assets` | asset del microsito |

### Schema

`schema_migrations` contiene la versione applicata. `page_views` e le vecchie
tabelle assistant/chatbot non fanno parte dello schema v9.

## Import/export

```text
JSONL              record canonici, niente binari/stato
mifp-content ZIP   scraper -> dashboard; record + asset
portable ZIP       dashboard -> dashboard; record + asset + stato durevole
host backup        DB SQLite + assets/conferences/config per disaster recovery
```

Sono ancora accettati vecchi ZIP `mifp-export` e ZIP scraper senza identificatore
di formato. I vecchi JSONL `_mifp` sono rifiutati intenzionalmente.
