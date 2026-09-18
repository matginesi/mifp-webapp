# Cookie Policy — MIFP

**Mediterranean Institute of Fundamental Physics**
*Last updated: June 2026*

---

## 1. What Are Cookies

Cookies are small text files that a website asks your browser to store. They are
sent back to the site on subsequent requests and can be used to keep you signed in
or to protect forms against cross-site request forgery. Most browsers accept
cookies automatically, and you can usually change your browser settings to refuse
or delete them.

This website does not use cookies for advertising, analytics, tracking, profiling
or marketing purposes, and it does not use third-party cookies of any kind.

## 2. Cookies on This Website

This website sets **two strictly necessary cookies**. Nothing else is stored on
your device by the site itself.

| Cookie name | Set when | Purpose | Duration | Type |
|-------------|----------|---------|----------|------|
| `mifp_admin_session` | Only after a **successful administrator login** | Keeps the authenticated dashboard session valid. Contains a signed session identifier; no personal data is stored in the cookie itself. | Browser session cookie (removed when the browser closes); the server also ends the session after 8 hours by default | Strictly necessary |
| `mifp_csrf` | Only on a page that renders a **form protected against cross-site request forgery** — currently the membership application form (`/join`), the administrator login form (`/login`) and the maintenance page | Carries a random value that the submitted form's signed CSRF token is bound to. This is what makes the token unusable from a different site. | Up to 2 hours | Strictly necessary |

Both cookies are `HttpOnly`, use `SameSite=Lax`, are scoped to the whole site, and
are marked `Secure` on the production HTTPS deployment.

### Ordinary browsing

Reading the public website — the home page, news, events, the archive, the member
directory, the research and publication pages, and the Privacy and Cookie policies —
does **not** set any cookie at all. `mifp_csrf` is only created when a form that
actually needs CSRF protection is rendered, so a visitor who only reads pages never
receives it.

### Membership application form

When you open the application form at `/join`, the page renders a CSRF-protected
form, so `mifp_csrf` is set for the duration of that submission. The cookie contains
no personal data; it is a random value that is discarded when it expires. The
details you type into the form are sent to MIFP's server and are covered by the
[Privacy Policy](/privacy), not by this cookie policy.

### Administrator session

`mifp_admin_session` is set only after a successful login to the administrative
dashboard. It is never sent to a visitor who has not logged in, and it grants no
access to the public site. Blocking it prevents dashboard access, which is expected
behaviour for a strictly necessary authentication cookie.

### Aggregate statistics

Public website statistics are produced exclusively as aggregate server-side daily
counters. They use no cookies, no visitor IDs, no IP addresses or IP hashes, no
User-Agent hashes, no full referrers, no query strings, no browser fingerprinting,
no tracking pixels and no third-party analytics service. Because they involve no
storage on, or reading from, your device, they are not cookies.

### Local storage

The **public website does not use `localStorage` or session storage at all**.

The administrator dashboard uses a single `localStorage` entry,
`mifp-dashboard-sidebar-collapsed`, to remember whether the navigation sidebar was
left collapsed. It is a purely client-side interface preference, it never leaves
your browser, it is not accessible to the public site, and it is not used to
identify or track anyone.

### Third-party services

This website does not embed external scripts, widgets, fonts or stylesheets. All
assets — stylesheets, JavaScript libraries, fonts and icons — are served from the
same server as the site. No external domain is contacted while you browse it. If
this ever changes, this policy will be updated before the new resource is deployed.

## 3. Legal Basis

Under the GDPR and the ePrivacy Directive, cookies that are strictly necessary to
provide a service explicitly requested by the user do not require prior consent.
Both cookies described above fall into that category: one authenticates the
administrator, the other protects forms against cross-site request forgery. Their
legal basis is MIFP's legitimate interest in operating the website securely
(Article 6(1)(f) GDPR), together with the necessity of the processing for the
requested service.

Because no optional, analytics or marketing cookies are used, this website does not
show a consent banner and does not need to: there is nothing optional to consent to.
If optional cookies are introduced in the future, a real consent mechanism will be
implemented first and this policy will be updated.

## 4. How to Control Cookies

You can view, block and delete cookies through your browser settings. Most browsers
allow you to:

- view the cookies stored on your device;
- block cookies from specific websites;
- delete all cookies when you close the browser;
- configure preferences for third-party cookies.

Blocking or deleting these cookies does not affect your ability to read the public
website, but it will prevent the administrator dashboard from working and will make
the membership application form fail its security check.

### Browser settings

- [Chrome](https://support.google.com/chrome/answer/95647)
- [Firefox](https://support.mozilla.org/en-US/kb/cookies-information-websites-store-on-your-computer)
- [Safari](https://support.apple.com/guide/safari/manage-cookies-and-website-data-sfri11471/mac)
- [Edge](https://support.microsoft.com/en-us/microsoft-edge/delete-cookies-in-microsoft-edge-63947406-40ac-c3b8-57b9-2a946a29ae09)

## 5. Changes to This Policy

We may update this cookie policy from time to time. When we do, the date at the top
of this page will be revised. We encourage you to review this policy periodically to
stay informed about how cookies are used.

## 6. Contact

If you have any questions about this cookie policy, please contact us at:

- **Organization:** Mediterranean Institute of Fundamental Physics (MIFP)
- **Address:** Via Appia Nuova 31, 00047 Marino (Roma), Italy
- **Email:** [info@mifp.eu](mailto:info@mifp.eu)
