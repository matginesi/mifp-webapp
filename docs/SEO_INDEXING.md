# SEO and search indexing

The public site keeps search-engine metadata derived from the same SQLite state
used to render the pages. There is no separate SEO database, no sitemap build
job and no Google-specific indexing queue.

## Runtime behavior

- `/robots.txt` points crawlers to the canonical sitemap. It deliberately keeps
  crawling available even when the dashboard indexing switch is off, because
  crawlers must be able to fetch a page to observe its `noindex` rule.
- `/sitemap.xml` is generated from published/active records. Dynamic news,
  events and sponsors use their actual `updated_at` timestamp as `lastmod`.
- Changes to linked assets or external links touch the parent public record so
  a meaningful presentation change is reflected in `lastmod` too.
- Query/filter/search variants emit `noindex,follow` and canonicalize to the
  clean route without the query string.
- Error, maintenance, login, dashboard, health/readiness and generated
  institutional PDF responses emit `X-Robots-Tag: noindex, nofollow` where
  applicable. Generated PDFs intentionally do not compete with their HTML page.
- Public pages emit self-referencing canonical URLs, Open Graph metadata and
  schema.org structured data. News use `NewsArticle`; events use `Event`;
  sponsors/home expose `Organization` data.
- Production Caddy permanently redirects the `www` hostname to the configured
  apex domain so only one public host serves indexable HTML.

## Dashboard

Open **Dashboard -> Operations -> SEO & Indexing**.

The page controls:

- public indexing enabled/disabled;
- canonical origin, normally `https://mifp.eu`;
- Google Search Console HTML-tag verification token;
- default meta description.

It also shows the current sitemap URL count, newest meaningful modification,
published-content diagnostics and a sitemap preview. Diagnostics do not invent
metadata: they point back to the existing content editor when a published news,
event or sponsor lacks useful fields. The panel also reports the current event
model limitation: free-text locations are useful Schema.org data, but Google
Event rich-result eligibility requires a structured `PostalAddress`.

## Google Search Console: one-time setup

1. Deploy the public site and confirm `https://mifp.eu/robots.txt` and
   `https://mifp.eu/sitemap.xml` are reachable.
2. In the SEO dashboard set the canonical origin to `https://mifp.eu` and keep
   public indexing enabled.
3. Add the site in Google Search Console. Prefer a Domain property when DNS
   verification is available. If using the HTML-tag method, copy only the
   `content="..."` token into the dashboard field, save, then verify in Search
   Console.
4. Submit `https://mifp.eu/sitemap.xml` once in the Sitemaps report.
5. Use URL Inspection for important pages after launch or when diagnosing a
   specific page. Routine content updates continue through the same sitemap and
   do not require manual resubmission.

Do not add Google's Indexing API for normal MIFP pages. Google documents that
API for `JobPosting` pages and livestream pages using `BroadcastEvent` inside a
`VideoObject`; it is not the general-purpose indexing mechanism for this site.

## Operational checks after a deploy

```bash
curl -fsS https://mifp.eu/robots.txt
curl -fsS https://mifp.eu/sitemap.xml | head -80
curl -fsSI https://www.mifp.eu/ | grep -Ei '^(HTTP/|location:)'
curl -fsSI https://mifp.eu/health | grep -i '^x-robots-tag:'
```

For a public page, inspect the rendered HTML and verify that `canonical`,
`og:url`, the robots directive and JSON-LD all use the same canonical host.
