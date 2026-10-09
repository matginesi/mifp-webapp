# Debug and security

## Debug mode

Debug is controlled only by `runtime.debug` in `conference.yaml`.

When enabled, the interface exposes a Debug button opening a totem with:

- runtime version and viewport;
- active theme and palette;
- resolved root/config/CSV paths;
- People CSV header validation;
- Program duplicate/orphan ID diagnostics;
- enabled/disabled section switches;
- full parsed YAML snapshot;
- buffered JavaScript logs;
- debug-only theme/palette selectors;
- link to the debug-only UI Kit;
- log download and reload/reset actions.

With debug disabled, the Debug button and theme/palette testing controls are not rendered. A directly opened `ui-kit.html` only displays an unavailable notice.

## JavaScript logging

`js/site.js` uses `trace`, `debug`, `info`, `warn`, `error`, and `silent`. Debug can use a more verbose threshold than production through `runtime.debug_log_level`.

Unhandled JavaScript errors and unhandled Promise rejections are also captured in the in-memory log buffer.

## Dynamic content safety

YAML/CSV text is escaped before being inserted into generated HTML. Unsafe URL schemes are rejected. Insecure external HTTP links are rejected; external links use HTTPS plus `noopener noreferrer` where applicable.

The lightweight YAML parser rejects prototype-pollution keys. Generated Program CSV files use CSV escaping and spreadsheet formula-prefix protection.

## Maps

Optional map embeds are limited by CSP and runtime validation to HTTPS OpenStreetMap embed URLs. They can be disabled with the relevant YAML map switches. When enabled, the visitor browser contacts OpenStreetMap to retrieve the embedded map.

## Public static data

`Visible=false` / `Visibile=false` is a presentation switch, **not access control**. Source CSV files remain public if deployed in the document root. Do not publish private records, credentials, API keys or confidential files.

## Server-side hardening

Use HTTPS. Configure HSTS, MIME types, compression, cache policy and any additional security headers on the web server/reverse proxy. Browser-side JavaScript cannot replace server-side TLS/security configuration.

## v1.5 visual diagnostics

The Debug totem deliberately avoids raw configuration JSON. It presents:

- runtime/page/viewport counters;
- clickable theme preview cards;
- clickable palette swatches with live application;
- enabled/disabled section pills;
- resolved configuration/CSV paths;
- People CSV required-header checks;
- Program duplicate-ID/orphan checks;
- privacy/runtime feature status;
- structured recent log entries with compact key/value context;
- direct access to the debug-only UI Kit.

The downloadable log remains JSONL because it is intended for machine-readable diagnostics, but raw JSON is not used as the primary debug UI.
