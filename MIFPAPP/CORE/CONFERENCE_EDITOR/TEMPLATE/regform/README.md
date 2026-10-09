# MIFP Secure Registration Form

Self-contained PHP registration module. Public conference content remains in `../conference.yaml`; **all operational form settings live separately in `regform/settings.yaml`**.

## Configuration split

`../conference.yaml` contains participant-facing registration information: fees, deadlines, payment instructions and the public link to `regform/`.

`settings.yaml` contains only the isolated form configuration:

```yaml
regform:
  enabled: true
  submit_enabled: true
  back_url: ../registration.html
  privacy_url: ../privacy.html
  mail:
    from_email: secretary@mifp.eu
    from_name: MIFP Registration
    reply_to_email: contact@example.org
    admin_emails:
      - secretary@mifp.eu
    subject_prefix: "[MIFP]"
    send_user_confirmation: true
  backend:
    max_upload_mb: 5
    storage_path: registrations
    persist_submissions: true
    rate_limit_requests: 5
    rate_limit_window_seconds: 900
    minimum_fill_seconds: 2
    trust_proxy: true
    trusted_proxies:
      - 127.0.0.1
      - "::1"
```

Form fields and their allowed select values are also defined under `regform.sections` in the same dedicated file. PHP reads `conference.yaml` only for public conference identity/registration information and `regform/settings.yaml` for the form itself.

`settings.yaml` is denied by `regform/.htaccess` and must never contain SMTP passwords or other secrets. Mail transport credentials belong in the server configuration, not in YAML.

## Local test

Run the conference root with PHP:

```bash
php -S 127.0.0.1:8000
```

Then open `http://127.0.0.1:8000/regform/`.

## Security

- CSRF token with secure session cookies (`HttpOnly`, `SameSite=Strict`, `Secure` on HTTPS).
- Persistent per-IP rate limiting; forwarded IP headers are trusted only from explicitly configured proxies.
- Honeypot and minimum form-fill time.
- Server-side allowlist validation for select values and strict date/email validation.
- Proof-of-payment upload limit plus MIME/signature validation; only PDF, JPEG and PNG are accepted.
- Organizer notification and participant confirmation are separate messages.
- `settings.yaml` and `registrations/` are denied by Apache rules.
- PHP errors are logged server-side and are not rendered to visitors.

## Storage

The same WEBSITE package supports both the MIFP VPS and shared hosting:

- **MIFP VPS / dedicated PHP-FPM:** when `MIFP_REGISTRATION_DIR` and `MIFP_UPLOAD_DIR` are provided by the host, submissions and proof uploads are written to per-event private directories outside the public document root.
- **Shared hosting / Aruba fallback:** when those variables are absent, `backend.storage_path` is used relative to `regform/` (by default `regform/registrations/`) and proof files remain under its protected `proofs/` subdirectory.

The event key is derived from the conference directory name and appended to the host private roots, preventing collisions between conferences. Host-provided private directory variables must be absolute paths.

SMTP credentials are never stored in this package. The form uses PHP `mail()`; on the MIFP VPS the host routes it through the centrally configured SMTP relay, while shared hosting may provide its own mail transport.

## PHP

Designed for PHP 7.4+ (including PHP 8.x) and standard `session`, `filter` and `fileinfo` extensions. If ext-yaml is unavailable, the module uses its bundled limited YAML parser.
## Appearance and form schema

The registration page inherits `appearance.default_theme`, `appearance.default_palette`, theme/palette tokens and conference branding from `../conference.yaml`. Form sections and fields are rendered and validated from `regform/settings.yaml`; removing an optional section does not require editing PHP.

## Aruba / shared hosting

The bundled `.htaccess` intentionally uses only `mod_rewrite` rules. Do not add `Options -Indexes` or Apache authorization directives such as `Require all denied` on Aruba shared Linux hosting: unsupported directives can make the whole `regform/` directory return HTTP 500. Sensitive `settings.yaml`, `src/`, and `registrations/` paths are denied through rewrite rules; PHP files also contain direct-access guards.


## PHP 7.4 compatibility

The module intentionally avoids PHP 8-only syntax and runtime helpers. `mixed` type declarations and native `str_contains()` / `str_starts_with()` calls are not used; small prefixed compatibility helpers provide the required string checks. This keeps the same code deployable on legacy PHP 7.4 shared hosting and on current PHP 8.x installations.


## Upload limits and deployment

`backend.max_upload_mb` is the requested cap, not a promise about the host.
The UI, client validation and PHP use the minimum of that cap (at most 20 MiB),
`upload_max_filesize`, and `post_max_size` minus 1 MiB for multipart overhead.
On the MIFP VPS configured at 10M/12M, a requested 16 MiB cap allows 10 MiB.
A POST beyond the server cap gets an explicit size error before CSRF validation.

Caddy does not process `.htaccess`: its host-managed rules must deny
`regform/settings.yaml`, `regform/src/`, `regform/registrations/` and dotfiles
before the approved regform rewrite. `conference.yaml` remains public.
Private storage roots and the PHP allowlist belong to the host. Never include
runtime submissions, rate files, secrets or proof uploads in WEBSITE packages.
PHP `mail()` hands messages to the host relay; acceptance does not guarantee
final delivery. Relay attachment limits must accommodate base64 overhead
(roughly 14 MiB plus headers/body for a 10 MiB proof).

The template starts with `submit_enabled: false`. The operator explicitly opens
submissions in the Editor's Regform page after configuring the conference.
No automatic date-based opening/closing is implemented: public deadlines are
information; `enabled` and `submit_enabled` control availability.

Runtime tests also run on PHP 8.x; use `MIFP_TEST_PHP=/path/to/php8.3` to verify
against the target runtime. The array form of `session_set_cookie_params` used
here requires PHP 7.3+.


## Registration administration and notification outcomes

MIFP's dashboard reads the private CSV and payment proofs through read-only
mounts; administrative states and notes remain separate from the CSV. The
regform records organizer and participant transport outcomes separately in
`REG-*.notifications.json`. `accepted` means transport acceptance, not inbox
delivery. Failed or interrupted SMTP attempts remain `unknown`; they are not
retried automatically. Host operators must investigate ambiguous outcomes before
attesting non-acceptance and approving a new retry. A saved registration is
retained when either email channel fails.

The event `.operations.lock` coordinates submissions with explicit host cleanup
and retry; per-receipt notification locks prevent concurrent attempts. On the
VPS, `MIFP_REGISTRATION_READ_GROUP=1` selects 0640 files / 0750 storage directories
for read-only dashboard access. Without this host opt-in, shared-hosting storage
keeps 0600 / 0700. SMTP credentials remain entirely outside WEBSITE exports.
When available, fsync synchronizes persisted data before transport attempts;
PHP 7.4 retains the portable fflush path without requiring newer runtime APIs.
