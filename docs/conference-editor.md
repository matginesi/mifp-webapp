# Conference Editor integrato

Aprire **Dashboard → Conference Editor**. L'accesso alla pagina, alle librerie,
ai template ZIP/DOCX e alla preview richiede la stessa sessione amministrativa
della dashboard. L'Editor mantiene il proprio layout e lavora sui file del
browser: nessun progetto viene scritto nel filesystem del server.

## Sorgenti e funzionalità

La copia canonica è `MIFPAPP/CORE/CONFERENCE_EDITOR/`, separata dal package
Python `mifp_app/`. La cartella contiene soltanto sorgenti, asset e template;
non va sostituita con un symlink al sibling. Un eventuale collegamento Git al
repository originale resta una scelta successiva: oggi tutti i file sono
versionati direttamente nel repository MIFP e disponibili al checkout CI.
Include il codice
Editor 1.23.4, le librerie locali ZIP/YAML/CSV/XLSX/documenti, il template del sito
con RegForm e i sorgenti dei due template DOCX. La licenza originale è conservata.
Il sibling originale, comprese le sue modifiche locali, non è stato cancellato
né modificato durante questa migrazione. Non deve più essere usato per lo
sviluppo ordinario dopo aver verificato l'integrazione.

| Capacità | Comportamento integrato |
|---|---|
| New Conference | Template canonico con placeholder, People/Program vuoti e invio chiuso; scrittura nella cartella scelta nel browser compatibile |
| Open Conference | File System Access oppure importazione cartella/ZIP nel browser |
| YAML e contenuti | Editing visuale e sorgente, validazione e Content Check |
| People, Speakers, Program | Gestione esistente, import/export CSV e XLSX |
| Asset e immagini | Gestione locale, nessun upload implicito sul server |
| RegForm | Impostazioni, campi e apertura originali |
| Preview | Sito dinamico e preview del form, senza esecuzione PHP o invii |
| Documenti | Badge, certificati, PDF, DOCX renderizzati e template DOCX |
| WEBSITE | ZIP completo del microsito, compatibile con l'importatore esistente |
| INFO | Pacchetto istituzionale separato per Events; non sostituisce il WEBSITE |

Gli archivi del template vengono costruiti in memoria dai sorgenti immutabili,
con ordine e timestamp deterministici, e memorizzati nella cache del processo.
Non si versionano ZIP generati. I template DOCX sono conservati come XML e
risorse estratte, poi ricomposti nel formato DOCX alla richiesta autenticata.
Non servono Node, PHP o un servizio aggiuntivo nel container Flask.

## Preview e isolamento

L'Editor stesso è una route dedicata, senza iframe. La **preview del contenuto
importato** usa invece un frame sandbox in una finestra dedicata: può eseguire
il JavaScript locale del microsito, ma non condividere origine, cookie, storage
amministrativo o accesso al DOM della dashboard. Gli asset sono incorporati e le
preferenze della preview vivono in memoria. La sua CSP dedicata consente questi
asset soltanto sulla route di preview, blocca rete e submit; la policy delle
altre pagine resta invariata. Mappe e risorse esterne non vengono caricate nella
preview isolata; il comportamento del sito esportato resta quello originale.

New Conference con scrittura diretta richiede File System Access, come prima.
Nei browser senza questa API restano Open tramite cartella/ZIP ed export tramite
download. Le funzioni PDF/DOCX restano nel browser e non trasmettono dati a servizi
esterni. Chiudere le finestre Editor/preview al termine della sessione: i dati già
caricati nel browser restano locali anche dopo il logout.

**Open conference** usa il selettore nativo con accesso in scrittura, per salvare
direttamente nella cartella scelta. Se il browser non completa questa operazione,
usare **Open folder copy**: il selettore cartelle compatibile legge i file nel
browser; le modifiche restano nella sessione e si salvano esportando WEBSITE ZIP.
Non viene copiata la cartella sul server. Selezionare PLMCN-2027 (contenente
conference.yaml), non una sua sottocartella assets o regform.

Il pannello Logs distingue apertura del selettore, cartella selezionata,
annullamento e mancato accesso. Un errore di permessi non viene trattato come
assenza di conference.yaml; una selezione nativa fallita conserva il progetto già aperto.

## Copia del progetto originale e WEBSITE esistenti

L'Editor riparte dai sorgenti del repository `mifp-conference-editor`, con il suo
layout e i suoi comandi originali. Il collegamento nella sidebar apre la route
autenticata. Non è presente il comando aggiunto “Update RegForm runtime”, né
un nuovo workflow di pubblicazione. Gli adattamenti si limitano al caricamento
Flask/CSP, al logging condiviso e all'isolamento della preview sull'origine
amministrativa. TEMPLATE e librerie sono copiati dall'originale, senza patch PHP
specifiche della precedente integrazione. Il sibling non viene modificato.

Aprire ed esportare un WEBSITE mantiene il suo PHP originale: per aggiornare un
vecchio modulo occorre confrontarne i sorgenti con il TEMPLATE aggiornato prima
di esportare. Conservare settings.yaml, personalizzazioni e dati; esportare una
copia di sicurezza prima di intervenire. Il bootstrap del TEMPLATE originale
supporta `MIFP_REGISTRATION_READ_GROUP=1`, necessario insieme al pool aggiornato
per mantenere la lettura del CSV dopo gli invii sulla VPS.

Esportare WEBSITE e importarlo tramite **Conference sites**, con revoca PHP prima
della sostituzione e nuova approvazione dopo revisione. INFO resta un passaggio
istituzionale separato. Seguire il
[runbook VPS](deployment/regform-dashboard-update.md).

## Packaging Docker

Il Dockerfile copia esplicitamente `CONFERENCE_EDITOR/` in
`/app/CONFERENCE_EDITOR/`, oltre a `mifp_app/`. Flask usa lo stesso percorso
relativo alla root CORE in locale e nel container; non occorrono mount o copie
manuali sulla VPS. La pipeline include tutti i sorgenti e asset senza dipendenze
dal sibling o da GitHub Pages. Il build context resta CORE e il compose produzione
continua a usare esclusivamente immagini GHCR. Un futuro submodule richiederebbe
anche di predisporre il checkout CI: non è stato introdotto con questa patch.

## Logging e console

L'Editor usa il logger JavaScript condiviso di MIFP, caricato dalla route Flask:
nessuna seconda copia di `logger.js` nella cartella Editor. La console usa eventi
`editor.*` e la stessa redazione di credenziali/email delle altre pagine. In
produzione mostra warning/error; con Flask DEBUG mostra anche info/debug.
Il pannello Logs dell'Editor conserva gli eventi locali con dettagli redatti,
anche quando il livello console li filtra. Non li invia al server.

Gli errori JavaScript, promise non gestite, asset mancanti e richieste HTTP fallite
usano la diagnostica condivisa. I guasti dei grafici registrano
`charts.library_unavailable` o `charts.render_failed`, senza etichette o valori
dei registranti. I log browser riguardano l'Editor/dashboard; la preview isolata
e i micrositi esportati mantengono un contesto separato.

Sul server `registrations.storage_unavailable` include motivo sicuro e request
ID. Il formato console Python ora espone anche il nome evento, così è ricercabile
in `docker logs` come nel JSON. Il compose produzione conserva `LOG_OUTPUT=stdout`
e `LOG_CONSOLE_FORMAT=text` per impostazione predefinita; il driver Docker gestisce
la rotazione. Non servono file host aggiuntivi, credenziali o servizi di logging.

## Verifica e dismissione del progetto precedente

Test browser opzionali, con Chromium installato e dati sintetici:

```bash
MIFP_TEST_EDITOR_BROWSER=1 .venv/bin/python -m pytest TESTS/webapp/test_editor_browser.py -n 0 -q
```

Per verificare una cartella autorevole locale senza modificarla, aggiungere
`MIFP_TEST_EDITOR_PROJECT=/percorso/PLMCN-2027`. I test escludono registrazioni e
upload, usano una copia temporanea per il selettore compatibile e non esportano
dati verso servizi esterni. Il selettore nativo è simulato: il dialogo del sistema
operativo e i permessi del browser dell'operatore richiedono verifica sul posto.

Dopo deploy e verifica di New/Open, preview e export, l'operatore può disabilitare
il workflow Pages e la pubblicazione nelle impostazioni del vecchio repository,
aggiornare i vecchi collegamenti e archiviarlo in sola lettura. Prima conservare
commit, modifiche locali e progetti autorevoli non versionati. Non cancellare
repository remoti, storico o progetti locali come parte dell'aggiornamento MIFP.
