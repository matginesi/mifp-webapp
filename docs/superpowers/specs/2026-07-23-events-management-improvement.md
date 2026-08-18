# Event Management Improvement — Design Spec

## Goal
Improve conference/event management with a dedicated dashboard page, 3-step creation wizard, typed document management (Program, Proceedings, Brochure, Poster), and grouped document display on the public event detail page.

## Scope
- **New** dashboard page at `/dashboard/events` (separate from the generic `content.html` editor)
- **New** 3-step wizard modal for event creation
- **New** document type selection when uploading PDFs (type stored in `assets.caption`)
- **New** grouped document display on public `event_detail.html`
- **Zero** schema changes, migrations, or dead code
- **Zero** modifications to existing routes (`/dashboard/content/events` remains untouched)

## Design

### Dashboard page (`/dashboard/events`)
- Tabbed layout: "Forthcoming" and "Past" tabs
- Each event shown as a card with: title, dates, location, event_type badge, cover thumbnail, document count
- "New Event" button opens the 3-step wizard
- Clicking an event card opens an edit modal

### 3-step creation wizard
- Step 1: Type & Details (event_type select, title, series_key, slug auto-generated, review_status)
- Step 2: Dates & Location (start_date, end_date, date_precision, date_text, location, remote_url)
- Step 3: Documents & Cover (upload cover image, upload PDFs with type picker)

### Document type picker
- On upload, user selects type: Program, Proceedings, Brochure, Poster, Other
- Type stored in `assets.caption` (existing field, previously used for image alt text — empty for PDFs)
- Displayed as color badges in edit modal and public page

### Public event detail page
- Document section grouped by type with icons:
  - 📋 Program
  - 📄 Proceedings
  - 📎 Brochure
  - 🖼 Poster
  - 📁 Other

## Files

### New files
- `templates/dashboard/events.html` — Event list page
- `templates/dashboard/_event_wizard.html` — 3-step wizard modal
- `templates/dashboard/_event_edit.html` — Edit modal with document management

### Modified files
- `mifp_app/routes/dashboard_content.py` — Add `/dashboard/events` route
- `mifp_app/static/js/dashboard.js` — Add wizard logic + document type picker
- `mifp_app/static/css/dashboard.css` — Add wizard + badge styles
- `mifp_app/templates/public/event_detail.html` — Grouped document display
- `mifp_app/services/public_repository.py` — Enrich documents with type label

## Non-goals
- No schema changes
- No migration scripts
- No refactoring of existing code
- No "NEW" badge on public events
