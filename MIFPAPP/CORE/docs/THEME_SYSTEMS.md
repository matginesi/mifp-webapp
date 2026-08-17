# MIFP theme systems

This document is the visual and interaction contract for the public website and the administrative dashboard. New UI must reuse the tokens and component grammar defined here. Page-local styling is allowed only for layout that is genuinely unique to that page.

## Shared identity

Both themes represent the same institution and therefore share:

- MIFP red `#a72b31` for identity and primary action;
- deep navy/charcoal for institutional structure;
- cool neutral borders rather than warm decorative surfaces;
- compact, precise geometry;
- Inter for interface/body text;
- visible but restrained keyboard focus: a crisp 2 px ring shown only through `:focus-visible`, minimum 44 px touch targets on public/mobile controls, and reduced-motion support;
- sentence-case labels and action names that describe the result.

The themes are related, not identical. The public site is editorial and atmospheric. The dashboard is an operational tool.

---

## Public theme: Scientific atlas

### Purpose and audience

The public site serves researchers, members, partners, event attendees, and readers discovering MIFP. Its main job is to establish institutional identity and make research, people, events, publications, and news easy to scan.

### Visual thesis

The design behaves like a contemporary scientific atlas: dark field, precise rules, high-contrast editorial typography, coordinates and worldlines used as meaningful scientific references. The home-page worldline is the signature element. Other components remain quiet so it retains impact.

### Tokens

| Role | Token | Value |
| --- | --- | --- |
| Canvas | `--ink-900` | `#0b1019` |
| Raised canvas | `--ink-850` | `#101722` |
| Panel | `--ink-800` | `#151d2a` |
| Border | `--line` | `#293241` |
| Primary text | `--paper` | `#f4f5f7` |
| Body text | `--paper-mute` | `#cbd0d8` |
| Muted text | `--paper-dim` | `#929baa` |
| Brand/action | `--mifp-red` | `#b92b22` |
| Reference/link | `--mifp-blue` | `#3f7ae0` |
| Keyboard focus | `--focus-ring` | `#86a8eb` |

Typography:

- display `--f-display`: Iowan Old Style / Palatino / Georgia, used for page titles, editorial headings, research titles and authored institutional prose;
- body `--f-body`: Inter, used for navigation, ordinary editorial summaries, forms, controls and supporting copy;
- data `--f-data`: system monospace, used only where fixed-width scanning materially helps: coordinates, dates, identifiers and compact labels.

Institutional pages deliberately use more serif than news, events and
directories: their hero introduction and continuous document body are serif,
while archive navigation, metadata, controls, tables and technical references
remain sans-serif. Institutional PDF exports mirror this division. Operational
database PDF/DOCX exports use serif for the MIFP/title block and sans-serif for
metadata, tables and page furniture.

Geometry:

- `--r-xs: 3px` for tiny controls;
- `--r-sm: 4px` for inputs, buttons, tags, and nested panels;
- `--r: 6px` for cards;
- `--r-lg` and `--r-xl: 8px` for large containers and dialogs;
- circles are reserved for inherently circular objects such as avatars, status dots, and icon-only close controls;
- pills are reserved for short status/category labels, never general containers.

### Surfaces and gradients

Cards, forms, navigation, banners, and content panels use flat color. Decorative gradients are not a component style. The home hero may use the scientific field/worldline treatment as the single atmospheric exception. Images may naturally contain gradients; UI chrome may not.

### Components

- Buttons share one height, radius, weight, and focus treatment. The focus ring is 2 px, blue and offset by 2 px; dark navigation may use the same ring inset. Primary means one main action; ghost means lower priority.
- Cards use one border, one radius, and no glow. Hover changes border/color by a small amount and does not make content jump.
- Section headings use typography and a rule, not ornamental capsules.
- The cookie notice is a compact institutional panel aligned with the same card geometry. Its close control remains icon-only with a clear accessible label.
- Lists prefer rows and dividers when the information is comparable; cards are used for genuinely self-contained editorial objects.

### Public page families

- **People directory:** Members are presented as a compact scientific
  directory, with portrait/initials, name, affiliation and role read in that
  order. The desktop grid uses three legible columns rather than a wall of
  small profile tiles; mobile becomes a single-column register.
- **Institutional network:** Sponsors are partner records, not promotional
  logo tiles. Logo, organization name, description and profile action belong
  to one bordered registry entry. Sponsor details and the quick-view lightbox
  use the same identity block.
- **Institutional archive:** About, Manifesto, Conduct, Research,
  Publications, Sponsorship, Privacy and Cookies share the `MIFP archive`
  index. Long-form policy pages pair a restrained metadata rail with a single
  readable document column. Repeated floating cards must not fragment a
  continuous document.
- **Research dossier:** Research opens with a compact, database-derived
  institute profile. Areas are presented as indexed scientific records with
  scope, supporting material and source actions; publication history and
  member geography form one evidence section rather than a detached dashboard.

The archive index is the signature shared element for inner institutional
pages. It encodes actual information architecture and should not be copied to
unrelated content such as news or events.

### Motion

Use one short transition curve for focus, hover, and disclosure. Avoid rotating or bouncing decorative icons. Respect `prefers-reduced-motion`; content and controls must remain fully understandable without animation.

### Public-page checklist

- Uses only theme tokens for color, radius, shadow, and motion.
- No new gradient unless it extends the home hero's scientific field.
- No arbitrary radius or pill-shaped card.
- Heading hierarchy remains semantic and visible.
- Links remain distinguishable without relying only on color in running text.
- Mobile controls are comfortably tappable and no horizontal scrolling is introduced.

---

## Dashboard theme: Control instrument

### Purpose and audience

The dashboard serves authenticated administrators managing content, imports, assets, quality review, privacy, logs, and system operations. Its main job is to make state, scope, consequence, and the next safe action immediately clear.

### Visual thesis

The dashboard behaves like a calibrated control instrument: dark persistent shell, flat light workspace, crisp borders, dense but breathable data, and semantic status colors. Decoration never competes with operational meaning.

### Tokens

| Role | Token | Value |
| --- | --- | --- |
| Workspace | `--content-bg` | `#eef1f4` |
| Primary surface | `--surface` | `#ffffff` |
| Secondary surface | `--surface-2` | `#f7f8fa` |
| Strong border | `--border` | `#c7cdd5` |
| Soft border | `--border-soft` | `#e1e5ea` |
| Primary text | `--text-bright` | `#181b20` |
| Body text | `--text` | `#2c3036` |
| Muted text | `--text-2` | `#5f6670` |
| Shell | `--shell-bg` | `#181b20` |
| Primary action | `--accent` | `#a72b31` |
| Keyboard focus | `--focus-ring` | `#456f9d` |

Geometry:

- `--radius-sm: 3px` for controls, chips, nested rows;
- `--radius: 5px` for cards, toolbars, tables, and modals;
- `--radius-lg: 7px` only for large workflow containers;
- status pills and circular progress/status marks are the only fully rounded elements.

### Color rules

- red: primary action, destructive emphasis, brand selection;
- green: completed/safe/success;
- amber: warning or human attention required;
- blue: neutral information and links;
- purple/orange only when a distinct semantic category already exists;
- never use a gradient to imply importance or state.

### Typography

- Dashboard interface, headings, tables, controls and messages use `--font-family-base` (Inter/system sans-serif).
- Identifiers, log context, timestamps and machine values use `--font-family-mono` only when alignment or scanning benefits.
- `--font-family-editorial` is reserved for previews of public-facing content inside the dashboard; it must not leak into operational UI.

### Page anatomy

Every dashboard page follows the same sequence:

1. page header: eyebrow, title, one-sentence purpose, primary actions;
2. optional summary strip with the few metrics needed for the current decision;
3. toolbar/filter controls;
4. primary working surface (table, list, editor, or workflow);
5. contextual help, history, or secondary actions.

Do not introduce a bespoke hero. Operational pages do not need marketing treatments.

The shared page header is a white instrument plate with one thin MIFP-red
calibration rail. It is the same on editorial lists, editors, workflows and
system pages. Summary strips use the same border, radius and low elevation;
their cells may change content, but not their visual grammar.

### Components and interaction

- Use `page_header` for all page titles/actions.
- Use `modern-card` for primary surfaces and `panel-head` for their headings.
- Toolbars contain filters and bulk controls, not explanatory paragraphs.
- Tables keep headers sticky when the surrounding component already scrolls; actions use consistent verbs.
- Primary action appears once per local context. Destructive actions use danger styling and confirmation.
- Empty states say what is empty and provide the next action when one exists.
- Loading state preserves context and names the current operation.
- Success/error messages repeat the action vocabulary used by the initiating control.
- Modal footer order is Cancel, then the outcome action. Password confirmation is reserved for sensitive data operations.
- Modals use the dark administrative shell as their header, a red state rule, a scrollable white body, and a fixed light action footer.
- Logs show human-readable message and structured context first; the original raw record stays collapsed as a diagnostic fallback.
- A dark command strip is reserved for an active or protected operational workflow (for example Data Quality continuation or password-gated safety operations). It always uses the dashboard shell color and red calibration rail; it is not a page-specific hero.

### Navigation UX

- Sidebar groups remain Content, Operations, and System.
- The active item uses a solid accent rail and tonal background, not a gradient.
- The top bar contains global actions only: find, public site, session/logout.
- Page-specific actions stay in the page header.
- On mobile the sidebar becomes a modal drawer with backdrop and returns focus to the toggle when closed.

### Dashboard-page checklist

- No literal decorative gradient in dashboard UI.
- No page-specific radius when a token works.
- Uses the standard header, card, toolbar, form, table, badge, empty, modal, and alert primitives.
- Status is communicated by text/icon as well as color.
- Icon-only controls have accessible names and adequate targets.
- Destructive or sensitive actions communicate consequence before execution.
- Filters, pagination, selection, and bulk actions retain visible state.
- Mobile layout keeps the primary action and current context reachable.

### Page-family coverage

The contract is checked across every dashboard destination, grouped by the job
it performs:

- directory and search: Dashboard, global search;
- editorial ledgers: Members, News, Events, Publications, Sponsors, Research Areas, Conference Sites;
- policy editors: Institutional Pages, Privacy & Cookies, Site Texts;
- operational workspaces: Assets, Import / Export, Data Quality, Join Requests;
- reports and diagnostics: Statistics, Logs, Server;
- Control Centre: Overview, Processes, Content Quality, Asset Health, Storage,
  Site Readiness, Incidents, Backups, Configuration, and Protected Operations.

New destinations must fit one of these families or document why a genuinely new
interaction primitive is necessary.

---

## Maintenance rules

The canonical runtime stylesheets are:

- `mifp_app/static/css/homepage.css` for the public site and login;
- `mifp_app/static/css/dashboard.css` for the dashboard.

Source-section comments inside those files are organizational markers, not separate files. When adding a component:

1. reuse an existing token and primitive;
2. add layout-only component CSS in the relevant section;
3. avoid element selectors that leak into unrelated pages;
4. test desktop and mobile widths;
5. test keyboard focus and reduced motion;
6. update this document only when the system itself changes, not for a one-off page.

### CSS ownership and retirement

- A visual primitive has one canonical definition. Do not append a second
  “refinement”, compatibility layer, or page-local copy at the end of a
  stylesheet.
- Responsive changes stay beside the component when practical; the final
  responsive section is reserved for shared layout breakpoints.
- Selectors retained only for old templates must be removed with those
  templates. Before removal, search templates, JavaScript and Python-generated
  markup; naming a class in CSS alone is not usage.
- Runtime templates may load only the two canonical theme files above, plus the
  isolated `work-in-progress.css` used exclusively by its standalone page.
- Vendor styles remain vendored and are not copied into theme files. Overrides
  are scoped to `.public-site` or `.dashboard-shell` and kept as small as
  possible.
- Token values are declared once near the beginning of the stylesheet. A later
  `:root` block that silently changes the palette, radius or shadow contract is
  considered a regression.

For a cleanup pass, verify selector references, exercise the affected page at
desktop and mobile widths, check keyboard focus and run the UI contract tests.
Dynamic state classes such as `is-active`, status levels and Data Quality types
must be traced through JavaScript/server rendering before removal.

Automated contract tests should reject reintroduced dashboard gradients, uncontrolled radii, missing focus treatment, or templates that bypass the shared page header without a documented reason.
