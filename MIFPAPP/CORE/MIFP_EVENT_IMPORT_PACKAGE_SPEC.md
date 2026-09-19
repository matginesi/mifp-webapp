# MIFP event import package specification

This is the operator and producer contract for **Dashboard → Events → Import
Event**. The dashboard validates every package again immediately before import.
An error blocks the operation; a warning requires explicit acknowledgement.

## 1. Package names

Use `<EVENT>_WEBSITE.zip` and `<EVENT>_INFO.zip`, for example
`PLMCN-2027_WEBSITE.zip` and `PLMCN-2027_INFO.zip`. Names help operators, but
the importer detects package roles from their contents.

## 2. WEBSITE structure

The archive must contain exactly one safe top-level event directory:

```text
PLMCN-2027/
├── index.html
├── conference.yaml
├── conference.version.json       # optional
├── assets/…                       # optional
├── data/…                         # optional
└── regform/*.php                  # optional; disabled after import
```

`conference.yaml` and one root `index.html`, `index.htm`, or `index.php` are
required. Other public static files are optional. The root is the default public
path and is compared case-insensitively with the INFO slug.

## 3. INFO structure (`mifp-content` v1)

INFO uses the existing MIFP portability format; there is no event-specific
replacement format:

```text
manifest.json
records.jsonl
assets/…                           # when declared
```

`manifest.json` must declare `format: "mifp-content"`, `format_version: 1`,
and either `scope: "events"` or `scope: "all"`. The latter is accepted for
Conference Editor/MIFP exports only because this importer still requires
`records.jsonl` to contain exactly one `event` record.

## 4. Required and optional metadata

The event record requires a stable `data.uid`, lowercase hyphenated `data.slug`,
and the normal required event fields of the MIFP portability contract. Dates,
description, `remote_url`, links, assets, editor metadata, and structured people
use that existing contract. UID and slug must be reused on updates.

## 5. Manifest integrity

`records_sha256` must be the lowercase SHA-256 of the exact `records.jsonl`
bytes. Record counts, type counts, scope, and all declared asset entries must
match archive contents. Undeclared asset files are rejected.

## 6. `records.jsonl`

Use UTF-8 JSON Lines, one object per line. This import accepts one line whose
`type` is `event`. Unknown fields, malformed JSON, invalid dates/enums, and a
missing stable identity are errors. Existing MIFP import validation remains the
authoritative field contract.

## 7. Assets and SHA-256

Every packaged asset must appear below `assets/`, be declared in the manifest,
exist at the declared path, have the declared byte size, and match its SHA-256.
Conference Editor may mark an embedded record asset with `storage_status: "packaged"`;
the importer treats that as a transport state and normalizes it to local storage
after the asset is installed.
Missing, unexpected, or mismatched assets are errors.

## 8. `conference.yaml`

The file must be UTF-8 YAML containing a mapping and be at most 2 MiB. Producers
should include `site.title`, `site.short_name`, `site.base_url`, and
`conference.full_name`, `conference.acronym`, `conference.start_date`, and
`conference.end_date`. If present, `conference.version.json` must be valid UTF-8
JSON containing an object; its `version` is shown by the dashboard.

## 9. URL and path rules

The public host always comes from `EVENTS_DOMAIN`; a package cannot choose an
external deployment host. A destination is a relative path of at most four
segments. Each segment starts with an ASCII letter/digit and then uses only
letters, digits, `.`, `_`, `~`, or `-`. Absolute paths, drive paths, empty and
dot segments, and traversal are rejected. The canonical preview is
`https://<EVENTS_DOMAIN>/<path>/` (HTTP is used only for localhost).

An INFO `remote_url` for `events.mifp.eu` is metadata, not an instruction to
bypass the configured environment host. Thus the same package works on a
`.home.arpa` pseudo-VPS.

## 10. PHP and `regform/`

PHP source may be packaged, but execution is deny-by-default. Import never
edits Caddy or the host allow-list. If reviewed code must run, an operator uses
an explicit host action such as:

```bash
sudo mifpctl events-php-enable PLMCN-2027/regform
```

Private submissions must live in `/opt/mifp/events-private`, never in WEBSITE.
Runtime registration records under `regform/registrations/` are rejected. The
validator permits only a tiny public guard scaffold used by Conference Editor
(`.gitignore`, deny-only `.htaccess`, a 404-only `index.php`, and empty
`.gitkeep`/`.keep` placeholders). CSV/DB/proof/submission files and arbitrary
PHP below that directory remain hard errors.

The container receives the allow-list state as one read-only file. Publication
fails closed in production if that state is missing or if the selected event
already has an enabled PHP prefix; the operator must disable it before new code
can replace the directory.

## 11. Prohibited files

Packages must not contain `.env` files, repositories such as `.git`/`.svn`,
databases or journals, SQL dumps, backups, private keys/certificates, credential
or token files, password files, or private registration/submission data.

## 12. ZIP security

Members must be regular files/directories with relative POSIX paths. Symlinks,
hardlink/special-device encodings, sockets, FIFOs, absolute/drive paths,
traversal, NULs, and duplicate normalized paths are errors. File count,
unpacked size, upload size, and per-entry compression ratio are bounded by the
server configuration. CRC/integrity is checked before extraction.

## 13. Update and replace

“Reject if exists” is the default. “Replace/update atomically” affects only the
selected event directory; it never deletes sibling conference directories.
Metadata is upserted by UID, then slug, so a repeated INFO import updates rather
than duplicates the event.

## 14. Atomic import and rollback

The server uploads to private staging, validates, safely extracts on the same
filesystem as `EVENTS_ROOT`, starts the database transaction, prepares metadata,
renames only the selected event directory, and commits. On failure it rolls back
the database and newly-created assets, removes the failed directory, and restores
the prior directory. A retained rollback copy is scoped to that event.

## 15. PLMCN-2027 example

`PLMCN-2027_WEBSITE.zip` has root `PLMCN-2027/` and its INFO record uses slug
`plmcn-2027`; this case-only difference is valid. With
`EVENTS_DOMAIN=events.vpsbox.home.arpa`, its preview is
`https://events.vpsbox.home.arpa/PLMCN-2027/`, regardless of an older
`events.mifp.eu` URL stored in INFO.

## 16. Compatibility and versioning

WEBSITE layout is versioned by optional `conference.version.json`. INFO version
support is defined by the shared portability constants (`mifp-content` v1).
Unsupported versions fail closed. Add a new supported version to the shared
parser before producing it; do not silently reinterpret an existing version.

## 17. PASS, WARNING, and ERROR

- **PASS:** structural, integrity, identity, and security checks succeeded.
- **WARNING:** safe but reviewable differences, such as title/URL-path drift or
  PHP source that will remain disabled.
- **ERROR:** unsafe ZIP/path/object, prohibited/private material, bad YAML/JSON,
  integrity failure, missing asset, invalid UID/slug, multiple event records,
  identity/date mismatch, invalid destination, or an existing destination when
  replacement was not explicitly selected.

The executable limits, supported INFO version, and validation behavior live in
`mifp_app.services.event_import`, `data_portability`, and
`portability_contract`; this document describes those validators rather than
defining an independent schema.
