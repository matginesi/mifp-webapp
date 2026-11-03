# Privacy Policy — MIFP

**Mediterranean Institute of Fundamental Physics**
*Last updated: 3 November 2026*

---

## 1. Data Controller

The Mediterranean Institute of Fundamental Physics (MIFP), with registered office at **Via Appia Nuova 31, 00047 Marino (Roma), Italy**, is the data controller for the personal data processed through this website. MIFP handles personal data in accordance with the General Data Protection Regulation (GDPR) and applicable Italian data protection law (Legislative Decree 196/2003 as amended by Legislative Decree 101/2018).

For any question about this policy or about the processing of your personal data:

- **General inquiries:** [secretary@mifp.eu](mailto:secretary@mifp.eu)
- **Privacy inquiries:** [secretary@mifp.eu](mailto:secretary@mifp.eu)

## 2. Where the Data Comes From

This website holds personal data from three different sources, and they are not interchangeable.

**Data you provide directly.** Public collection includes membership applications at `/join` and shared-link surveys at `/survey/...`. The membership application records your first and last name and e-mail address, plus any affiliation, country, field of study, position and free-text motivation you choose to provide. If e-mail delivery is enabled, the same application details are sent to MIFP's configured administrative mailbox so that the request can be reviewed. The platform also stores the username and password hash of the MIFP administrator account.

**Historical and imported institutional data.** MIFP's catalogue of past events, conferences and schools is built from MIFP's own historical archives and from previous MIFP websites. Imported records may therefore contain the names, affiliations and roles of people who took part in past MIFP activities, together with related programme and publication metadata. This data was not collected through this website and was not supplied by the individuals concerned through it.

**Publicly available professional and scientific information.** Some records are assembled from publicly available professional and scientific sources, such as published conference programmes, proceedings and institutional pages.

Imports also keep a provenance record of what was read and when, including the raw payload received from the source. That record is what makes the archive verifiable and reproducible, and it can contain the same personal data as the canonical record. It is retained for archive integrity and is covered by the retention discussion in §7.

The website also provides Polls for preliminary interest, availability and other purposes stated in each survey notice. A Polls response is not an enrolment or a booking unless the specific notice explicitly states otherwise. This site has no general enquiry form.

### Shared-link Polls

Each survey displays its applicable privacy information before submission. Surveys retaining individual responses present controller/contact, purpose, legal basis, recipients/processors, transfers and retention date, with legitimate-interest details and DPO contact where applicable. Aggregate-only surveys present the organiser, purpose, aggregate storage behaviour, retention information and the link to the website technical-processing policy. The administrator must complete the required disclosures and explicitly review the rendered notice before publishing; imported YAML and saved Drafts do not approve legal information. A snapshot of the published notice is retained with that survey.

In **aggregate-only anonymous mode** (available for polls without free-text questions), Polls stores per-question and per-date counts instead of individual answers, answer combinations or submission times. A list of random retry identifiers is retained without any mapping to answer choices, to avoid duplicate counts after HTTP retries. Rare or unique categories in a very small respondent population can still permit inference about respondents; this option is not an unconditional legal guarantee of anonymisation. Website technical/security data is processed separately as described below. Aggregate-only reports and exports contain totals rather than individual response rows.

In **anonymous mode**, Polls stores answers, a random request ID for safe retry handling, the calendar submission date and the notice-version reference. Names, email addresses, IP addresses and user-agent information are not attached to anonymous survey answers. Please do not volunteer identifying or sensitive information in free-text answers. In **identified mode**, Polls additionally requests first name, last name and self-declared email; identity and membership are not authenticated.

Here, “anonymous mode” means collection without requested names or email addresses; it is not a guarantee of effective anonymisation. Free text and distinctive combinations of answers may identify a respondent. The stored submission day and append order may also permit correlation with separately held technical logs, particularly for small groups. The retry ID belongs to one submission, not an individual invitation, and the strictly necessary CSRF cookie is not stored with answers. Application survey access logs omit client IP/fingerprints and redact the shared-link key, but separate hosting, proxy or security logs must also be considered. Consequently the current mode alone does not exempt a survey from applicable personal-data disclosure checks.

Invitations can be distributed externally using Aruba mailing lists. The application does not import that recipient directory or send Polls invitations. A shared survey URL is accessible to anyone who possesses it: distribution to members does not make it membership-restricted. No participant tracking or advertising is added.

Administration of the Aruba mailing list and processing of survey responses are separate activities. A mailing-list legal basis is not automatically the legal basis for survey answers. Genuinely anonymous information falls outside the GDPR, but this must be established from the actual answers and reasonably possible correlations, not inferred from the response-mode label.

Survey responses are stored on MIFP-managed infrastructure in Italy. That location alone does not establish the absence of international transfers: actual administrative access, processors and backup arrangements must be considered in the survey's disclosures. The Polls-specific legal basis, applicable interest and transfer assessment must be settled for the actual processing; this policy does not automatically assign a basis or declare transfers absent.

## 3. Technical Data and Aggregate Statistics

The platform does not use analytics or profiling cookies, persistent visitor identifiers, localStorage analytics, browser fingerprinting, tracking pixels or third-party tracking services. Public website statistics are produced only as server-side aggregate daily counters, without IP addresses, IP hashes, User-Agent hashes, full referrers, query strings or reconstruction of individual navigation paths.

Separate technical security and access logs exist to protect and operate the service. They may contain:

- the request path, method, status code, response size and duration;
- a **pseudonymous** client fingerprint derived from the IP address with a server-side salt, used for rate limiting and abuse detection;
- for authenticated actions, the administrator username and the action performed.

A pseudonymous identifier is not an anonymous one: it still relates to a single client and can be linked across requests. The logs are used for security and operations only, never for profiling or analytics.

Two further points of detail:

- Storing the **raw** IP address of a membership application is **disabled by default** (`JOIN_STORE_RAW_IP`). IP addresses are still processed transiently for rate limiting and abuse prevention.
- Logging the raw client IP into the log streams is also off by default (`LOG_INCLUDE_CLIENT_IP`); the salted fingerprint is used instead.

## 4. Special-Category Data

MIFP does not ask for special-category personal data (health, biometric data, political opinions, religious beliefs, trade-union membership, sexual orientation, genetic data) and does not need it in order to consider a membership application.

Because membership motivations and survey answers may contain free text, please **do not include** special-category information. Polls is not designed to collect such data. Information voluntarily supplied in a membership application is handled as part of that application.

## 5. Purposes of Processing

Personal data is processed for these purposes only:

- reviewing and administering membership applications and the member register;
- collecting and analysing survey responses for the specific purpose displayed in each Polls notice;
- maintaining MIFP's institutional record of past events, conferences, schools and publications;
- operating and securing the website, including administrator authentication, rate limiting and abuse prevention;
- complying with legal, tax and accounting obligations.

## 6. Legal Basis

| Processing | Basis |
|------------|-------|
| Reviewing a membership application and administering the resulting membership relationship | Steps taken at the applicant's request before entering into a membership relationship, and performance of that relationship (Article 6(1)(b) GDPR) |
| Historical and imported institutional records of past MIFP activities | Legitimate interest in maintaining MIFP's institutional and scientific memory (Article 6(1)(f) GDPR) |
| Individual Polls responses, where personal data may be processed | The specific basis selected and reviewed for that survey, displayed before submission; no basis is inferred from membership or anonymous mode |
| Aggregate-only Polls results | Stored only as separate answer-option counts, without individual answer vectors; assess whether small-group results could remain identifiable. Website technical/security processing is described separately above |
| Website security, rate limiting and technical logs | Legitimate interest in operating a secure service (Article 6(1)(f) GDPR) |
| Legal, tax and accounting obligations | Legal obligation (Article 6(1)(c) GDPR) |

A Polls respondent consent checkbox is required only when consent is the explicitly selected basis for that processing, as described in the individual notice. Where applicable, consent can be withdrawn without affecting the lawfulness of earlier processing. The current anonymous Polls workflow does not support consent-based collection because it cannot ordinarily identify a submission for individual withdrawal.

The membership form does not request consent for an unrelated or optional purpose: submitting it asks MIFP to take the steps needed to assess the application. The platform does not rely on Article 9 GDPR and neither requests nor requires special-category data.

## 7. Data Retention

| Data | Retention |
|------|-----------|
| Membership register | Duration of membership plus 2 years |
| Membership applications that were rejected or archived | Up to 2 years from the decision, then eligible for deletion by the maintenance cleanup |
| Membership applications pending or under review | Kept until a decision is recorded |
| Technical access/security logs | 30 days by default; rotated files are removed by the maintenance cleanup |
| Aggregate public statistics | Daily counters retained for 730 days; they contain no personal data |
| Polls responses and survey metadata | Until the date shown in the individual notice; an administrator must delete the stored survey JSON/JSONL files by that date. This is manual deletion, not a scheduled automatic task |
| Database backups | Retained for the configured backup cycle, then aged out; see §10 |

Membership and log retention are enforced by the **protected maintenance cleanup**, which an operator runs deliberately from the dashboard and which always creates a verified snapshot first. It is not a continuously running scheduled job, and the application never deletes membership applications on its own.

Polls deletion uses its protected administrator deletion action or an explicit maintenance command, separately from membership cleanup. Downloads/exports and backup copies must be handled by their holders. Anonymous responses cannot ordinarily be located later by a person's identity; contact the controller for the applicable options.

You may request deletion of your personal data at any time using the contact address in §15.

## 8. Your Rights Under the GDPR

- **Access** — obtain a copy of the personal data we hold about you
- **Rectification** — have inaccurate or incomplete data corrected
- **Erasure** — request deletion of your data
- **Restriction** — limit how your data is used
- **Portability** — receive your data in a structured, machine-readable format
- **Objection** — object to processing based on legitimate interest
- **Complaint** — lodge a complaint with the Italian Data Protection Authority (Garante per la protezione dei dati personali)

These rights apply subject to the conditions and exceptions in the GDPR. To exercise them, contact [secretary@mifp.eu](mailto:secretary@mifp.eu). MIFP may need to verify your identity before acting on a request and will respond within the time limits established by applicable law.

Some of the historical and imported records described in §2 may relate to people we have no direct contact details for. Because the archive is the institutional record of past MIFP activities, such records may be restricted rather than deleted where erasure would destroy the scientific record; requests will be assessed individually.

## 9. Recipients and Service Providers

We do not sell, rent or share personal data for marketing or advertising purposes. Personal data may be processed by:

- **Hosting provider** — the infrastructure on which this website runs;
- **E-mail/SMTP provider** — used to deliver administrator notifications for membership applications and security/operational incidents. Security alerts may contain the minimum technical information needed to investigate an event (for example the source IP address of a failed or successful administrator login); passwords, session tokens and application secrets are never included;
- **Backup/storage provider** — where an off-site backup destination is configured;
- **Public authorities** — where we are legally required to disclose information.

The current providers, the existence of data processing agreements and any transfer assessment are organisational decisions that cannot be established from this repository. They are tracked in `docs/PRIVACY_DECISIONS_REQUIRED.md`. Where personal data is transferred outside the EEA, an appropriate Chapter V safeguard will be put in place before the processing starts.

## 10. Backups

Operational deletion removes records from the live database or, for Polls, from the survey's JSON/JSONL files, but a copy may remain in an integrity-protected backup until that backup ages out of the normal retention cycle. Backups are kept for disaster recovery, are not used for any other purpose, and are not browsable as a data source. Deleting a record therefore takes effect immediately in normal use and finally once the relevant backups have expired.

## 11. Data Security

Technical and organisational measures include:

- HTTPS for all communications, with HSTS on the production deployment;
- server-side password hashing using **PBKDF2-HMAC-SHA256 with 600,000 iterations** (Werkzeug-compatible `pbkdf2:sha256` format), stored as a hash only, never as a password;
- CSRF protection on every state-changing form, using signed, expiring tokens bound to the requesting client;
- rate limiting and abuse controls on administrator login, membership applications and public Polls submissions;
- strict access controls on the administrative area;
- data minimisation: the application stores only the fields the workflow actually uses;
- no third-party scripts, analytics or tracking services, and no external asset domains: stylesheets, JavaScript, fonts and icons are all served from this site.

## 12. Automated Decision-Making

We do not use automated decision-making or profiling based on personal data. Membership applications are reviewed by a person.

## 13. Cookies

This website sets **only strictly necessary cookies**: `mifp_admin_session`, created after a successful dashboard login, and `mifp_csrf`, a short-lived cookie created only on pages that render a form needing cross-site request forgery protection (including `/join`, `/login`, public survey forms at `/survey/...` and the maintenance page). Ordinary public browsing sets no cookie at all.

There are no analytics, advertising, marketing, profiling or third-party cookies. The website shows a short informational notice about these strictly necessary cookies. It is not a consent request: closing it stores only the notice revision in your browser's `localStorage`, so the same revision is not shown again. The value is not sent to MIFP or used for tracking. No optional-cookie consent is requested because there is no optional storage or tracking to authorise. Any processing consent required by a particular survey is a separate matter, described in that survey's notice. Full details, including cookie names, lifetimes and the notice preference, are in the [Cookie Policy](/cookie-policy).

## 14. Changes to This Policy

Any change to this Privacy Policy will be posted on this page, and the revision date at the top will be updated.

## 15. Contact and Complaints

- **Mediterranean Institute of Fundamental Physics (MIFP)**
- **Address:** Via Appia Nuova 31, 00047 Marino (Roma), Italy
- **Email (general):** [secretary@mifp.eu](mailto:secretary@mifp.eu)
- **Email (privacy):** [secretary@mifp.eu](mailto:secretary@mifp.eu)

You also have the right to lodge a complaint with the Italian Data Protection Authority (Garante per la protezione dei dati personali) at [www.garanteprivacy.it](https://www.garanteprivacy.it).
