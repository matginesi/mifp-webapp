# Ripristino futuro dei siti storici

Il backup sanitizzato non è ancora disponibile e non viene importato da questa
release. L'architettura attuale supporta già i siti statici a
`https://events.mifp.eu/<slug-originale>/`: Caddy serve direttamente la directory
sotto `/srv/mifp-events`, con `index.html` o `index.htm`. Flask non serve questi
file. Non occorrono un database degli archivi, nuove route, redirect o modifiche
Caddy. Il publisher WEBSITE esistente resta l'unico percorso di pubblicazione.

## Preparazione locale del backup

1. Conservare l'originale privatamente e lavorare su una copia sanitizzata.
   Inventariare gli slug e gli URL originali, rispettando maiuscole/minuscole.
   Ogni pacchetto deve avere una sola directory radice con quel nome. Non
   rinominare gli slug per adattarli alla dashboard senza una revisione degli URL.
   I segmenti accettano lettere/cifre e `._~-`, iniziano con lettera/cifra;
   traversal, percorsi assoluti, symlink e collisioni richiedono arresto/revisione.
2. Conservare HTML, CSS, JavaScript revisionato, immagini e documenti pubblici,
   mantenendo struttura e percorsi relativi. Controllare riferimenti assoluti,
   domini esterni e differenze di case. Rimuovere tracking, codice di invio form
   e dipendenze da servizi dinamici; una pagina pubblica può contenere dati
   personali e richiede revisione anche quando il formato è statico.
3. Escludere PHP e altri eseguibili, script mail, CGI, include, credenziali,
   `.env`, configurazioni SMTP/DB, dump, log, backup, sessioni, cache, file nascosti,
   CSV di iscrizione, ricevute private e directory di registrazioni/upload privati.
   Non considerare il blocco HTTP una sostituzione della sanitizzazione.
   Il validatore ZIP rifiuta numerose categorie sensibili, ma non certifica che
   ogni HTML/JS/documento sia privo di segreti o dati personali.
4. I vecchi RegForm devono restare inattivi. Preservare, quando possibile, una
   presentazione **statica** con avviso di iscrizioni chiuse: rimuovere i form di
   invio e gli handler JS di submit/fetch. Una directory `regform/` può avere un
   `index.html` di consultazione. Non includere `settings.yaml` o script PHP per
   far funzionare il vecchio modulo. Verificare anche `mailto:` e link ad altri
   moduli di iscrizione: non devono riaprire involontariamente il percorso.
5. Il WEBSITE richiede un `index.html`/`index.htm` statico e un solo
   `conference.yaml` alla radice della conferenza. Per un sito legacy che ne è
   privo, aggiungere nella copia sanitizzata soltanto metadati descrittivi reali:

   ```yaml
   site:
     title: "<titolo originale verificato>"
   conference:
     event: "<slug originale verificato>"
   ```

   Sostituire i segnaposto dai documenti ricevuti; non inventare date o contenuti.
   Questo file descrive il pacchetto, non rigenera il sito né aggiunge un RegForm.
   Non sovrascrivere un `conference.yaml` già usato dal sito. Se ne esistono più
   copie o ci sono nomi/estensioni bloccati ma pubblicamente necessari, fermarsi
   per una conversione revisionata; non indebolire il validatore.

## Prova e pubblicazione successiva

- Prima della VPS, validare e pubblicare ogni WEBSITE in un ambiente locale
  isolato con database e destinazione temporanei. Importare solo WEBSITE:
  INFO è facoltativo e separato; non serve per mantenere gli URL originali.
  Non utilizzare l'Editor per rigenerare HTML legacy.
- Verificare link, immagini, CSS, JS e documenti tramite HTTP, comprese pagine
  annidate e relativi `../`. Disabilitare qualsiasi destinazione esterna di
  invio durante le prove. Una prova con un semplice file server verifica gli
  asset ma non prova le regole di sicurezza Caddy: usare anche il routing
  versionato nell'ambiente isolato.
- Creare un ZIP della sola copia revisionata, ad esempio sul computer locale,
  dopo aver impostato `slug` e `archive` su nomi verificati:

  ```bash
  python3 -m zipfile -c "$archive" "$slug"
  python3 -m zipfile -l "$archive"
  ```

  Il comando include tutto sotto la directory: eseguirlo soltanto dopo la
  sanitizzazione. Conservare l'originale fuori dall'albero del pacchetto.
- Sulla VPS esistente, dopo backup e preflight della procedura ordinaria,
  controllare le autorizzazioni correnti con `sudo mifpctl events-php-list`.
  Un archivio non deve comparire nell'allowlist. Se una vecchia autorizzazione
  coincide con lo slug, revocarla con il comando esistente
  `sudo mifpctl events-php-disable "$slug/regform"` prima di procedere.
  Non eseguire mai `events-php-enable` per un archivio.
- Importare da **Conference sites → Import WEBSITE / INFO**, con destinazione
  esattamente uguale allo slug originale e **First publication — reject if
  destination exists**. Il wizard controlla anche la destinazione sul filesystem
  VPS; l'assenza della riga dashboard non prova che il percorso sia libero.
  Un conflitto deve fermare l'import: non scegliere replace automaticamente,
  non cancellare il sito esistente. Se una sostituzione è realmente necessaria,
  serve una decisione esplicita separata, backup, PHP revocato e rollback.
- Verificare l'URL pubblico originale e i singoli asset. Il sito statico deve
  rispondere; richieste a script legacy `.php` devono restare 404, senza sorgente
  PHP. POST alla presentazione statica non deve elaborare iscrizioni. File
  privati devono essere assenti dal pacchetto e dall'albero pubblico.
  Ricontrollare l'allowlist e gli altri siti senza modificarli.

## Limiti delle pagine dinamiche

PHP, ASP, CGI, database, endpoint AJAX e form server-side non funzionano invariati
come archivio statico. Servono conversione locale revisionata e controllo dei link.
Gli URL `.php` rimangono bloccati: non introdurre eccezioni Caddy o approvazioni
PHP per preservarli. La disponibilità del sito allo slug originale non garantisce
che ogni vecchio URL dinamico possa essere mantenuto senza una decisione separata.

Slug, contenuti, dipendenze e conversioni effettive saranno verificati quando
arriverà il backup sanitizzato. Nessun sito storico o contenuto VPS è stato
creato, migrato o sostituito per questa attività.
