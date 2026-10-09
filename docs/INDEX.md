# Documentazione MIFP

Questa cartella contiene le guide e i riferimenti da mantenere insieme al codice.
Per avviare il progetto, partire dal [README principale](../README.md).

## Applicazione e dati

- [Riferimento CLI](cli-reference.md)
- [Schema database e lifecycle](database-schema.md)
- [Formato di import](import-format.md)
- [Gestione dei conference sites](conference-sites.md)
- [Conference Editor integrato](conference-editor.md)
- [Istruzioni per l'estrazione JSONL](scraping_prompt.txt)

## Deployment e manutenzione

- [Aggiornare la VPS esistente: procedura operativa](deployment/update-existing-vps.md)

- [Avvio su una VPS nuova](DEPLOY_NEW_VPS.md)
- [Installazione VPS e VM locale](deployment/vps-installation.md)
- [Micrositi, routing e autorizzazioni PHP](deployment/conference-microsites.md)
- [Registrazioni e affidabilità email](deployment/registrations.md)
- [Aggiornamento RegForm, dashboard ed Editor sulla VPS](deployment/regform-dashboard-update.md)
- [Backup e ripristino](deployment/backups.md)
- [Hardening](deployment/hardening.md)
- [Riferimento deployment](../DEPLOYMENT.md)

## Frontend, privacy e revisioni

- [Guida di stile frontend](FRONTEND_STYLE_GUIDE.md)
- [Revisione della coerenza frontend](FRONTEND_COHERENCE_REVIEW.md)
- [SEO e indicizzazione](SEO_INDEXING.md)
- [Decisioni privacy da confermare](PRIVACY_DECISIONS_REQUIRED.md)
- [Audit di sicurezza](audit/MIFP_PRODUCTION_SECURITY_AUDIT.md)
- [Revisione della manutenibilità](audit/MIFP_MAINTAINABILITY_REVIEW.md)

## Materiali locali: `local/`

`docs/local/` è esclusa da Git. Usarla per rapporti di singole sessioni,
annotazioni della workstation, patch destinate ad altri progetti, bozze e piani
degli agenti. I materiali già presenti sono suddivisi in `reports/`, `patches/`
e `superpowers/`.

Le guide versionate devono essere utilizzabili senza questa cartella e non
devono dipendere da file locali. Credenziali e dati operativi continuano a
seguire le regole di storage del progetto: questa cartella non le sostituisce.
