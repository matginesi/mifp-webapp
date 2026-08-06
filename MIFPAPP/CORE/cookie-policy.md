# Cookie Policy — MIFP

**Mediterranean Institute of Fundamental Physics**
*Last updated: June 2026*

---

## 1. What Are Cookies

Cookies are small text files stored on your device by your web browser when you visit a website. They help the website remember your actions and preferences over time, such as login status and language preferences. Most browsers accept cookies automatically, but you can usually modify your browser settings to decline them if you prefer.

This website does not use cookies for advertising, analytics, tracking, or profiling purposes.

## 2. Cookies on This Website

This website sets a session cookie only when you log in to the admin dashboard. For all other visitors — anyone browsing the public pages, reading news, viewing events, or searching the member directory — no cookies are set at all. We do not use advertising cookies, tracking cookies, social media cookies, or third-party analytics cookies.

Public statistics are generated exclusively as aggregate server-side counters. They do not use visitor IDs, analytics cookies, IP hashes, User-Agent hashes, full referrers, browser fingerprinting, tracking pixels, localStorage analytics, or third-party analytics services.

### Session Cookie (Admin Dashboard)

| Cookie Name | Purpose | Duration |
|-------------|---------|----------|
| `session` | Flask session cookie: keeps you logged in while navigating the dashboard. This cookie is set only after a successful login and is never sent to anonymous visitors. Expires when the browser is closed or after the configured session lifetime (8 hours by default). | Session |

This cookie is strictly necessary for the admin dashboard to function. It is never set for users who do not log in.

### No CSRF Cookie

Cross-site request forgery protection does not rely on a cookie. For anonymous visitors, CSRF tokens are generated using a stateless HMAC signature — no cookie is required. For logged-in administrators, the token is stored in the session cookie described above.

### Local Storage

The public website does not use `localStorage`, session storage, tracking pixels, browser fingerprinting, or third-party analytics. The only use of `localStorage` is to remember whether you have dismissed the cookie information notice — a purely client-side preference with no data transmitted to the server. You can clear this preference at any time via your browser settings.

### Third-Party Services

This website does not embed external scripts, widgets, or resources from third-party services. All assets (stylesheets, JavaScript libraries, fonts, icons) are hosted on the same server. No external domain is contacted when you browse this website. If this changes in the future, this policy will be updated and appropriate consent will be requested before any new cookies are deployed.

## 3. Legal Basis

Under the GDPR and the ePrivacy Directive, essential cookies do not require prior consent because they are necessary for the functioning of the website. The legal basis for processing data through essential cookies is our legitimate interest in operating and securing the website (Article 6(1)(f) of the GDPR).

Because no cookies are set for anonymous visitors, no consent mechanism is necessary for general browsing of this site.

## 4. How to Control Cookies

You can control and delete cookies through your browser settings. Most browsers allow you to:

- View the cookies stored on your device
- Block cookies from specific websites
- Delete all cookies when you close the browser
- Set preferences for third-party cookies

Please note that blocking cookies may affect the functionality of the admin dashboard.

### Browser Settings

- [Chrome](https://support.google.com/chrome/answer/95647)
- [Firefox](https://support.mozilla.org/en-US/kb/cookies-information-websites-store-on-your-computer)
- [Safari](https://support.apple.com/guide/safari/manage-cookies-and-website-data-sfri11471/mac)
- [Edge](https://support.microsoft.com/en-us/microsoft-edge/delete-cookies-in-microsoft-edge-63947406-40ac-c3b8-57b9-2a946a29ae09)

## 5. Changes to This Policy

We may update this cookie policy from time to time. When we do, the date at the top of this page will be revised. We encourage you to review this policy periodically to stay informed about how we use cookies.

## 6. Contact

If you have any questions about this cookie policy, please contact us at:

- **Organization:** Mediterranean Institute of Fundamental Physics (MIFP)
- **Address:** Via Appia Nuova 31, 00047 Marino (Roma), Italy
- **Email:** [info@mifp.eu](mailto:info@mifp.eu)
