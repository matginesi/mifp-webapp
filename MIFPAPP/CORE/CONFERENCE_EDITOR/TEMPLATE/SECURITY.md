# Security & privacy

The conference website is static except for the isolated `regform/` PHP registration endpoint.

- No analytics or advertising trackers.
- No CDN JavaScript or CSS.
- No remote fonts.
- No third-party PDF library.
- Program PDF generation runs locally in the browser.
- Optional embedded maps are restricted to OpenStreetMap.
- Google Maps is opened only after an explicit user click.
- Dynamic HTML values are escaped before rendering.
- Unsafe `javascript:` / `data:` links are blocked by the runtime.
- Local runtime paths containing `..` are rejected.

`Visible=false` in a static CSV is not access control: if a CSV is deployed publicly, its source can still be downloaded by a visitor who knows the path. Never place private information in these files.

No payment-provider script or embedded payment form is present. Payment is completed externally; `regform/` stores registrations in its protected CSV/proof repository and sends confirmation/organizer email through the server mail transport.
