# PLMCN-2027 — Static Conference Website

Static MIFP conference website for **PLMCN-2027 — Quantum Optics School**.

Confirmed event data currently included:

- Belgrade, Serbia
- 7–10 April 2027
- venue: Majestic
- format: Quantum Optics School
- organizer information: MIFP

Everything not yet confirmed is explicitly shown as **TBC** or **TBD**. No speaker, committee member, fee, deadline, scientific title, hotel, travel detail or social activity is invented.

### Registration visibility

Use `registration.enabled: true` in `conference.yaml` to publish the registration cards/form. Set it to `false` to hide those details and route Registration buttons, `registration.html`, and direct `regform/` access to `registration-tbd.html`. The placeholder text is configured under `registration.unavailable`.


## Architecture

The conference site remains static. The only server-side component is the isolated `regform/` PHP registration handler; there is no Node runtime, application database, CDN or tracker.

- `conference.yaml` — conference configuration and text
- `data/people.csv` — speakers / committees
- `data/program.csv` — scientific program
- `assets/` — local files only
- `*.html` — page structure, always manually editable
- `js/site.js` — small static renderer/runtime
- `js/pdf.js` — dependency-free Program PDF generator
- `regform/` — isolated PHP registration page; public event data comes from `conference.yaml`, while form/mail/backend settings come from `regform/settings.yaml`

Any automatic block can be replaced by hand-written HTML: remove its `data-render` attribute and edit the block normally.

## People CSV

Header is fixed:

```csv
First Name,Last Name,Category,Role,Affiliation,Country,Image,Visible
```

The PLMCN-2027 example contains only generic placeholders such as `First Name`, `Last Name`, `Affiliation`. No real person is assumed. People use `assets/people/no_face.jpg` when the optional `Image` column is empty. Face images can be linked directly from `assets/people/`.

`people.show_country: true` enables countries globally, but a country is rendered only when that person's `Country` cell is non-empty.

## Program

`data/program.csv` is always the source used to render `program.html`.

The public download is PDF only and is controlled by YAML:

```yaml
program:
  download:
    enabled: true
    mode: generated
    label: Download program PDF
    local_file: assets/documents/program.pdf
    local_filename: PLMCN-2027-program.pdf
    generated_filename: PLMCN-2027-program.pdf
```

- `mode: local` downloads the PDF you place in `assets/documents/`.
- `mode: generated` creates a formatted PDF in JavaScript from the same `data/program.csv` used by the page.

The CSV is not exposed as a Program download button.

The generated PDF uses the MIFP red / dark-blue / black identity configured under `program.pdf.colors`, with the event title prominently displayed.

## Countdown

The template supports multiple live countdowns with days, hours, minutes and seconds. Page and sidebar visibility are controlled independently in YAML:

```yaml
countdown:
  enabled: true
  show_on_page: true
  show_in_sidebar: true
  update_interval_seconds: 1
  urgent_within_days: 7
  items:
    - label: PLMCN-2027 begins
      show_on_page: true
      show_in_sidebar: true
      date: 2027-04-07T09:00:00+02:00
      end_date: 2027-04-10T18:00:00+02:00
      type: event
```

## Debug

With:

```yaml
runtime:
  debug: true
```

a Debug button appears. The visual diagnostic totem provides runtime health, CSV validation, paths, enabled sections, logs, theme cards and palette cards. Theme/palette experimentation is intentionally available only in Debug mode.

`ui-kit.html` is also available from the Debug panel and uses the same production components/tokens as the real pages.

## Maps and privacy

No Google map is embedded. Optional map previews use OpenStreetMap. Google Maps can only be opened after an explicit user click on a directions link.

No analytics, advertising trackers, third-party JavaScript, CDN styles, remote fonts or marketing cookies are included.

## Starting a local test

Do not open the pages with `file://`, because browsers restrict `fetch()` of YAML/CSV. Serve the folder with any static HTTP server, for example:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000/`.

## Creating another conference

Duplicate the folder and change:

1. `conference.yaml`
2. `data/people.csv`
3. `data/program.csv`
4. files in `assets/`
5. any special HTML blocks you want to customize manually

No source-code change should be necessary for normal conference content.


## PLMCN-2027 demo content

The PLMCN-2027 variant deliberately uses visible TBC/TBD placeholders for unconfirmed people, programme details, partner logos and imagery. Placeholder people use the local `assets/people/no_face.jpg`; no person names or portraits are invented.

## Accommodation & visa information

The Accommodation page keeps the participant-facing PLMCN hotel-scam warning prominent and uses Hotel Majestic contact/direct-booking information as venue context. Participants remain responsible for arranging their accommodation.

Visa links in `conference.yaml` point to official Republic of Serbia government sources.


## Countdown timers

`conference.yaml` can enable any number of independent countdowns. `enabled` controls whether a timer exists; `show_on_page` and `show_in_sidebar` independently control its two presentation surfaces. The same two flags also exist globally under `countdown` to switch all page timers or all sidebar timers off at once. Past timers disappear from the sidebar automatically. `urgent_within_days: 7` makes a visible timer enter the illuminated urgent state during its final seven days.

## SEO

Each public HTML page includes static title, description, robots, Open Graph and Twitter metadata so essential SEO does not depend on JavaScript. `robots.txt` is included. `site.base_url` is intentionally `TBC`; add the real public URL before publishing and then add canonical URLs / a sitemap for the final domain.

## PLMCN content structure

The homepage follows the established PLMCN-2026 narrative: conference scientific scope, Quantum Optics School, At a Glance, live deadlines, Important Dates, Location, Program, Sponsors, Organizing Institutions, Committee, Invited Speakers, Abstract Submission, Venue & Accommodation, Visa, Social Program, then Registration. The homepage Registration block includes both Payment Methods and Registration Plans; the dedicated `registration.html` remains the detailed information page, while operational Registration CTAs open the isolated `regform/` PHP form directly.

The 2027 scientific description intentionally follows the established PLMCN scope around strong light-matter coupling, low-dimensional and photonic structures, nanophotonics, microcavities, quantum technologies and related emerging fields. Unconfirmed 2027-specific scientific details remain TBC.

## Selectable image galleries

The template uses one reusable YAML-driven gallery widget for the Quantum Optics School, Home Venue, Venue, Accommodation and Social Program imagery. Each gallery keeps one large selected image with a compact thumbnail strip underneath, previous/next controls, keyboard navigation and the existing image lightbox. Gallery items, captions and initial selection remain data in `conference.yaml`; ordinary conference customization does not require JavaScript changes.

## Image lightbox

Venue, Belgrade, Quantum Optics School and Social Program content images can be opened in a local modal by click, Enter or Space. The modal uses no external library and closes with the X button, backdrop or Escape. Speaker `no_face` images and small navigation logos are intentionally excluded.

## Abstract submission template

`assets/documents/PLMCN-2027_Abstract_Template.docx` is a local one-page A4 abstract template. The homepage Submission section links directly to this file, so no external download is required. Configure it under `abstract_submission.template_*` in `conference.yaml`.


## v1.5 presentation flow

The homepage follows the PLMCN information narrative and closes with a complete participant-facing Registration section. Important registration conditions, Payment Methods and Registration Plans are visible directly on the homepage; registration-plan CTAs open the isolated `regform/` PHP form directly; `registration.html` remains available for detailed fees, conditions and payment information. Venue, Accommodation, Quantum Optics School and Social Program use the shared YAML-controlled selectable gallery widget. Venue maps use OpenStreetMap previews with Google Maps opened only after explicit user interaction.


## Registration form

`registration.html` remains the participant-facing information page. Its Registration section opens the isolated `regform/` PHP page, which owns CSRF/session handling, validation, proof-of-payment upload, protected CSV/proof storage and email delivery. Public fees, deadlines and payment information remain in `conference.yaml`; operational form settings are separate in `regform/settings.yaml`. Set `regform.submit_enabled` there to enable or close submissions.

## Single configuration source

`conference.yaml` remains the single public website configuration source. `regform/settings.yaml` is intentionally separate and contains only the isolated PHP registration settings; there is no `settings.ini.php`, legacy `config.yaml` or legacy `deployment.js`.

## Page transition / first paint

The CSS first-paint tokens match the default Paper + MIFP appearance, so page navigation no longer flashes the old black Midnight background before `conference.yaml` is applied.

## v1.5 deadline visibility

Countdown placement is explicit. Each item has independent `show_on_page` and `show_in_sidebar` flags, while the top-level `countdown` block has the same two flags as global surface switches. `enabled` remains the logical switch for the timer itself. When a visible future target is seven days away or closer (configurable with `countdown.urgent_within_days` or per-item `urgent_within_days`), that displayed instance receives the urgent illuminated state. Motion is disabled automatically when the visitor requests reduced motion.
