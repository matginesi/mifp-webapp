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

The default `regform/registrations/` directory stores the CSV and proof files and is protected by `.htaccess`. An absolute writable directory outside the document root remains preferable where hosting allows it.

## PHP

Designed for PHP 7.0+ (including PHP 8.x) and standard `session`, `filter` and `fileinfo` extensions. If ext-yaml is unavailable, the module uses its bundled limited YAML parser.
## Appearance and form schema

The registration page inherits `appearance.default_theme`, `appearance.default_palette`, theme/palette tokens and conference branding from `../conference.yaml`. Form sections and fields are rendered and validated from `regform/settings.yaml`; removing an optional section does not require editing PHP.

## Aruba / shared hosting

The bundled `.htaccess` intentionally uses only `mod_rewrite` rules. Do not add `Options -Indexes` or Apache authorization directives such as `Require all denied` on Aruba shared Linux hosting: unsupported directives can make the whole `regform/` directory return HTTP 500. Sensitive `settings.yaml`, `src/`, and `registrations/` paths are denied through rewrite rules; PHP files also contain direct-access guards.


## PHP 7.0 compatibility

The module intentionally avoids PHP 8-only syntax and runtime helpers. `mixed` type declarations and native `str_contains()` / `str_starts_with()` calls are not used; small prefixed compatibility helpers provide the required string checks. This keeps the same code deployable on legacy PHP 7.x shared hosting and on current PHP 8.x installations.
