# Configuration notes

`conference.yaml` contains all automatic content and behavior.

For NEW-CONFERENCE, only confirmed information has been filled. Unknown values are deliberately `TBC`/`TBD`.

Important areas:

- `site` — browser/SEO metadata
- `conference` — event identity, dates, city and venue
- `appearance` — theme tokens and palettes
- `runtime` — debug, logging and CSV paths
- `hero` — hero content and optional background image
- `registration` — fees, deadlines, payment and placeholder registration page
- `people` — display rules for `people.csv`
- `program` — rendering and PDF-download behavior
- `venue`, `accommodation`, `social_program`, `travel` — participant information
- `privacy` — Privacy & Cookies page/banner
- `countdown` — live milestones

Every major section has `enabled: true/false`.

Do not put unconfirmed people, fees or dates into the example merely to fill visual space. Use `TBC` or `TBD` instead.


### Multiple countdowns

Countdowns are configured in `countdown.items`. Every timer supports `enabled`, `label`, `date`, `end_date`, `type`, `size`, `show_on_page`, `show_in_sidebar` and an optional per-item `urgent_within_days`. `enabled` is the logical switch for the timer itself; the two `show_*` flags independently control its page and sidebar instances. The same `show_on_page` and `show_in_sidebar` flags exist at the top-level `countdown` block as global surface switches. Use `primary: true` with `size: large` for the main conference timer. Deadline timers such as Early Bird can use `size: compact`. Past timers disappear from the sidebar. `countdown.urgent_within_days` defaults the urgency threshold for all timers. Older configurations using `show_on_home` are still accepted as a page-visibility fallback, but new conference files should use `show_on_page`.

```yaml
countdown:
  enabled: true
  show_on_page: true
  show_in_sidebar: true
  urgent_within_days: 7
  items:
    - id: conference
      enabled: true
      primary: true
      size: large
      show_on_page: true
      show_in_sidebar: true
      date: TBC
      type: event
    - id: early-bird
      enabled: true
      size: compact
      show_on_page: true
      show_in_sidebar: true
      date: TBC-03-14T23:59:00+01:00
      type: deadline
      provisional: true
```


## Registration form settings

Public registration content stays under `registration` in `conference.yaml`. The PHP form itself is configured separately in `regform/settings.yaml` under the top-level `regform` key. This dedicated file owns form availability, fields, privacy text used by the form, mail sender/reply-to/control recipients, upload/storage settings and rate limiting. The public frontend never reads this file.

## Registration, Venue and Social toggles (v1.5)

- `registration.home_compact`: compact Registration rendering on the homepage.
- `registration.plans_enabled`: show/hide registration plan cards.
- `registration.warnings_enabled`: show/hide the configured important-condition list.
- `venue.maps_enabled`: show/hide the OpenStreetMap venue map.
- `venue.features_enabled`: show/hide venue fact cells.
- `venue.gallery_enabled`: show/hide the venue gallery.
- `social_program.items_enabled`: show/hide social-event rows.
- `social_program.gallery_enabled`: show/hide the Social Program gallery.

All text, warnings, payment instructions and gallery paths remain in `conference.yaml`; removing a `data-render` attribute still hands the entire block back to manual HTML.


### Selectable galleries

Image sections can expose `gallery_enabled`, `gallery_label`, `gallery_initial` and an `images`/`gallery` list. The shared renderer shows one primary image plus a small selectable thumbnail strip. Venue and Social Program keep their existing `gallery_enabled` switches; School, Home Venue, Accommodation and home Social can use the same widget without adding page-specific JavaScript. Each image item should provide at least `src`, with `alt` and `caption` strongly recommended.
