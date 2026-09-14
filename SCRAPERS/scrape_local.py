#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, logging, re, warnings
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
import shutil
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

log = logging.getLogger(__name__)

HTML_EXT={'.html','.htm','.php','.asp','.aspx',''}
ASSET_EXT={'.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.zip','.rar','.jpg','.jpeg','.png','.gif','.webp','.svg','.mp4','.mov','.csv','.txt'}
LOCAL_MIFP_HOSTS={'mifp.eu','www.mifp.eu','old.mifp.eu','www.old.mifp.eu'}

# ── helpers ──────────────────────────────────────────────────────────
def clean(s): return re.sub(r'\s+',' ', (s or '')).strip()
def sha1(s): return hashlib.sha1(s.encode('utf-8','ignore')).hexdigest()

def _event_group_key(url: str) -> str:
    """Extract event site key from a page URL.

    Pages belonging to the same event share a URL prefix.
    e.g. OECS13/venue.html and OECS13/contacts.html → key='OECS13'
    """
    path = urlparse(url).path.rstrip('/')
    parts = [p for p in path.split('/') if p and p not in ('www.mifp.eu', 'old.mifp.eu')]

    GROUP_DIRS = {'events-conferences','events-meetings','events-schools',
                  'events-workshops','meetings','schools','workshops','conferences'}
    SUBDIR_KEYWORDS = {'committees','venue','registration','accommodation','travel',
                       'program','scientific-program','social-program','sponsor',
                       'gallery','photo','contact','speaker','abstract','proceeding',
                       'submission','important-dates'}

    event_parts = [p for p in parts if p not in GROUP_DIRS]
    if not event_parts:
        return path

    key_parts = []
    for p in event_parts:
        if '.' in p:
            continue
        if p.lower() in SUBDIR_KEYWORDS:
            continue
        if re.match(r'^\d{4}-\d{2}-\d{2}', p):
            continue
        key_parts.append(p)

    if not key_parts:
        for p in event_parts:
            if '.' in p:
                stem = p.rsplit('.', 1)[0]
                return stem
        return url

    return '/'.join(key_parts)

# ── Menu / footer / cookie lines to eliminate ───────────────────────
_MENU_LINES = {
    'mifp', 'code of conduct', 'research', 'mifp publications',
    "members' publications", 'projects', 'events', 'meetings',
    'schools', 'workshops', 'conferences', 'scientific council',
    'people', 'contacts', 'site map', 'join us', 'privacy policy',
    'sponsors', 'search', 'agree', 'i accept cookies from this site.',
}
_FOOTER_RE = re.compile(r'©\s*\d{4}', re.I)
_COOKIE_RE = re.compile(r'cookie', re.I)
_CITY_COUNTRY_RE = re.compile(r'^[A-Z][a-z]+\s+[A-Z][a-z]+\s*$')

# ── A. extract_main_content() ───────────────────────────────────────
def extract_main_content(soup, source_url=None):
    """Return (clean_text, list_of_lines) with noise removed."""
    # 1. Remove unwanted tags entirely
    for tag in soup.find_all(['script','style','noscript','nav','header','footer']):
        tag.decompose()

    # 2. Remove Joomla-specific noise containers
    for sel in [
        '.nav', '.navbar', '.navigation', '#nav', '#navbar',
        '.breadcrumb', '.breadcrumbs',
        '.search', '#search', '.searchbox',
        '.sidebar', '.side-bar', '#sidebar',
        '.cookie', '#cookie', '.cookie-banner', '#cookie-banner',
        '.cookie-notice', '#cookie-notice', '.cc-banner',
        '.moduletable_menu', '.customnomargintop',
        '.footer', '#footer', '.site-footer',
    ]:
        for el in soup.select(sel):
            el.decompose()

    # 3. Try preferred Joomla content containers in order
    container = None
    for sel in ['.item-page', '.blog', '.content', '.component',
                '#component', 'main', 'article']:
        found = soup.select_one(sel)
        if found:
            container = found
            break

    # 4. Heuristic fallback: pick element with best text/link density
    if container is None:
        best, best_score = None, -1
        for el in soup.find_all(['div','section','main','article']):
            if not isinstance(el, Tag):
                continue
            txt = el.get_text(' ', strip=True)
            links = el.find_all('a')
            link_chars = sum(len(a.get_text(' ', strip=True)) for a in links)
            text_chars = len(txt) - link_chars
            score = text_chars - (link_chars * 0.5)
            if score > best_score:
                best, best_score = el, score
        if best is not None:
            container = best

    # 5. Extract text lines
    raw = container.get_text('\n') if container else soup.get_text('\n')
    lines = [clean(x) for x in raw.splitlines() if clean(x)]

    # 6. Filter menu / footer / cookie lines
    filtered = []
    seen = set()
    for line in lines:
        low = line.lower().strip()
        # Skip exact menu lines
        if low in _MENU_LINES:
            continue
        # Skip footer-like lines
        if _FOOTER_RE.search(line):
            continue
        # Skip cookie notices
        if _COOKIE_RE.search(line) and len(line) < 120:
            continue
        # Skip very short lines that are just nav labels
        if len(line) < 4:
            continue
        # Deduplicate
        if low in seen:
            continue
        seen.add(low)
        filtered.append(line)

    return '\n'.join(filtered), filtered


# ── B2. Joomla home-page news extraction ──────────────────────────

_SIDEBAR_TITLES = {'solab', 'join us', 'sponsors', 'search', 'reserved area'}
_NEWS_SIDEBAR_SKIP = {'login', 'who\'s online', 'popular', 'latest',
                       'newsflash', 'banner', 'advertisement', 'archive'}


def _clean_text(s):
    """Normalize whitespace and strip."""
    return re.sub(r'\s+', ' ', (s or '')).strip()


def _extract_date_from_text(text):
    """Extract date string and precision from text.
    Returns (date_str_iso, precision) or ('', 'unknown')."""
    patterns = [
        # DD/MM/YYYY
        (r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', 'day',
         lambda m: f'{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}'),
        # DD-MM-YYYY
        (r'\b(\d{1,2})-(\d{1,2})-(\d{4})\b', 'day',
         lambda m: f'{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}'),
        # on DDth of Month YYYY  or  DDth Month YYYY
        (r'\b(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b', 'day',
         lambda m: _month_date(m[3], m[2], m[1])),
        # Month DD, YYYY  or  Month DDth, YYYY
        (r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b', 'day',
         lambda m: _month_date(m[3], m[1], m[2])),
        # DD Month YYYY (e.g. "22 December 2017")
        (r'\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b', 'day',
         lambda m: _month_date(m[3], m[2], m[1])),
        # the DDth of Month YYYY
        (r'\bthe\s+(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(January|February|March|April|May|June|July|August|September|October|November|December)\b', 'day',
         None),  # handled above
        # Month YYYY
        (r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b', 'month',
         lambda m: f'{m[2]}-{_MONTH_NUM[m[1].lower()]:02d}-01'),
        # YYYY (standalone 4-digit year, 1990-2030)
        (r'\b(20\d{2}|19\d{2})\b', 'year',
         lambda m: f'{m[1]}-01-01'),
    ]
    for pat, precision, formatter in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            if formatter:
                try:
                    return formatter(m), precision
                except Exception:
                    continue
            return '', precision
    return '', 'unknown'


_MONTH_NUM = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}


def _month_date(year_str, month_str, day_str):
    y = int(year_str)
    m = _MONTH_NUM.get(month_str.lower().strip(), 1)
    d = int(day_str)
    return f'{y}-{m:02d}-{d:02d}'


def _looks_like_news_module(mod):
    """Determine if a div.module contains news content vs sidebar/decoration."""
    h3 = mod.find('h3')
    if h3 and h3.get_text(strip=True).lower().strip() in _SIDEBAR_TITLES:
        return False

    text = mod.get_text(strip=True)
    if len(text) < 60:
        return False

    has_strong = bool(mod.find(['strong', 'b', 'span'], class_=lambda c: c and 'highlight' in c.lower()) if False else
                      mod.find(['strong', 'b', 'span']))
    has_link = bool(mod.find('a', href=True))
    has_img = bool(mod.find('img'))

    # Minimum: must have either a link, an image, or emphasized text
    if not (has_link or has_img or has_strong):
        return False

    # Skip pure navigation/footer modules
    low = text.lower()
    if low in {'home', 'search', 'login', 'register'}:
        return False

    return True


def _extract_joomla_news_title(mod):
    """Extract the news title from a Joomla module."""
    candidates = []

    # 1. Look for span.highlight-bold
    for hb in mod.find_all('span', class_=lambda c: c and 'highlight' in c.lower()):
        t = _clean_text(hb.get_text())
        if len(t) > 5:
            candidates.append(t)

    # 2. Look for strong/b tags (first significant one)
    for strong in mod.find_all(['strong', 'b']):
        t = _clean_text(strong.get_text())
        if len(t) > 5 and t not in candidates:
            candidates.append(t)

    # 3. Look for anchor text
    for a in mod.find_all('a', href=True):
        t = _clean_text(a.get_text())
        if 8 <= len(t) <= 150 and t not in candidates:
            candidates.append(t)

    # 4. Take first text that looks like a title from the module text
    if not candidates:
        text = mod.get_text('\n', strip=True)
        for line in text.split('\n'):
            line = _clean_text(line)
            if 8 <= len(line) <= 150 and re.search(r'[A-Za-z]', line):
                candidates.append(line)
                break

    # Take the first candidate, trim to 120 chars
    title = candidates[0][:120] if candidates else ''
    return title


def _guess_news_type_from_text(text):
    """Guess the type of news based on keywords."""
    low = text.lower()
    if any(k in low for k in ['passed away', 'obituary', 'in memoriam', 'died', 'death']):
        return 'institutional'
    if any(k in low for k in ['award', 'prize', 'congratulation', 'nobel',
                               'fellowship', 'honorary', 'honoris causa']):
        return 'award'
    if any(k in low for k in ['agreement', 'cooperation', 'memorandum',
                               'partnership', 'signed', 'signing']):
        return 'agreement'
    if any(k in low for k in ['publication', 'published', 'book', 'article',
                               'paper', 'letter', 'review']):
        return 'publication_highlight'
    if any(k in low for k in ['conference', 'workshop', 'meeting', 'school',
                               'symposium', 'plmcn', 'icp2dc']):
        return 'event_highlight'
    return 'general'


def _normalize_joomla_news_record(mod, index, source_url, root=None, out_dir=None):
    """Transform a Joomla module into a normalized news record."""
    title = _extract_joomla_news_title(mod)
    text = mod.get_text('\n', strip=True)
    body = re.sub(r'\n+', '\n\n', text).strip()

    # Extract date
    extracted_date, date_precision = _extract_date_from_text(text)
    date_is_inferred = 0 if extracted_date else 1
    date_text = extracted_date if extracted_date else ''

    # Extract images
    images = []
    for img in mod.find_all('img'):
        src = img.get('src', '')
        alt = _clean_text(img.get('alt', ''))
        if src:
            resolved = urljoin(source_url, src) if not src.startswith(('http://', 'https://', 'data:')) else src
            image = {
                'url': resolved,
                'download_url': resolved,
                'kind': 'image',
                'alt_text': alt,
                'caption': alt,
                'role': 'cover' if len(images) == 0 else 'gallery',
                'sort_order': len(images),
                'source_url': source_url,
            }
            if root is not None and out_dir is not None and not _add_local_path(image, resolved, root, out_dir):
                continue
            images.append(image)

    # Extract document links  
    documents = []
    for a in mod.find_all('a', href=True):
        href = a['href'].strip()
        resolved = urljoin(source_url, href)
        ext = Path(urlparse(resolved).path).suffix.lower().lstrip('.')
        if ext in {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'zip'}:
            doc = {
                'url': resolved,
                'download_url': resolved,
                'text': _clean_text(a.get_text()),
                'kind': ext,
                'caption': '',
                'sort_order': len(documents),
                'source_url': source_url,
            }
            if root is not None and out_dir is not None and not _add_local_path(doc, resolved, root, out_dir):
                continue
            documents.append(doc)

    # Extract external links (non-image, non-document)
    links = []
    for a in mod.find_all('a', href=True):
        href = a['href'].strip()
        if href.startswith('#') or href.startswith('javascript:'):
            continue
        ext = href.rsplit('.', 1)[-1].lower() if '.' in href else ''
        if ext in {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'zip',
                   'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'}:
            continue
        text = _clean_text(a.get_text())
        if text:
            links.append({
                'url': href,
                'text': text,
                'role': 'external',
                'source_url': source_url,
            })

    # Summary
    summary = body[:250] if body else ''

    news_type = _guess_news_type_from_text(text)

    # Build assets list (combined images + documents)
    assets = []
    for img in images:
        assets.append({**img, 'kind': 'image', 'sort_order': len(assets)})
    for doc in documents:
        assets.append({**doc, 'kind': doc['kind'], 'sort_order': len(assets)})
    for link in links:
        if _guess_news_type_from_text(link.get('text', '')) == 'document':
            assets.append({**link, 'kind': 'document', 'sort_order': len(assets)})

    has_body = len(body) > 50
    confidence = 0.75 if has_body else 0.35
    is_published = 1 if has_body else 0
    review_status = 'published' if has_body else 'needs_review'

    record = {
        'type': 'news',
        'source': 'local_joomla_home',
        'source_url': source_url,
        'title': title[:300],
        'summary': summary[:250],
        'body': body[:8000],
        'date': extracted_date,
        'date_text': date_text,
        'date_precision': date_precision,
        'date_is_inferred': date_is_inferred,
        'sort_order': (index + 1) * 10,
        'news_type': news_type,
        'is_published': is_published,
        'review_status': review_status,
        'images': images,
        'documents': documents,
        'links': links,
        'assets': assets,
        'quality_flags_json': json.dumps({
            'confidence': confidence,
            'method': 'joomla_module_extraction',
            'source_url': source_url,
            'has_body': has_body,
            'body_length': len(body),
            'num_images': len(images),
            'num_documents': len(documents),
            'num_links': len(links),
            'has_date': bool(extracted_date),
            'date_precision': date_precision,
        }),
    }
    return record


def extract_joomla_home_news(soup, source_url, root=None, out_dir=None):
    """Extract news items from Joomla home page module structure.
    Returns list of normalized news records."""
    if not soup:
        return []

    modules = soup.find_all('div', class_='module')
    news_records = []

    for mod in modules:
        if not _looks_like_news_module(mod):
            continue
        try:
            record = _normalize_joomla_news_record(mod, len(news_records), source_url, root=root, out_dir=out_dir)
            if record['title'] and len(record['body']) > 30:
                news_records.append(record)
        except Exception:
            continue

    return news_records


# ── A. classify() with priority logic ───────────────────────────────
def classify(path, title, text):
    """
    Priority: 1) path  2) title/h1  3) controlled text fallback
    Singleton types (code_of_conduct, privacy, manifesto, members,
    scientific_council) are NEVER matched from text content alone.
    """
    p  = path.lower().replace('\\','/')
    t  = (title or '').lower()
    # text is available but only used for non-singleton types
    # tx = (text or '')[:500].lower()

    # ── Code of Conduct (singleton) ──
    if 'mifp-code-of-conduct' in p:
        return 'code_of_conduct','policy'

    # ── Privacy (singleton) ──
    if any(k in p for k in ['mifp-privacy-policy',
                             'join-us-privacy-policy',
                             'privacy-policy']):
        return 'privacy','policy'

    # ── Members (singleton) ──
    if 'mifp-members' in p or p.endswith('people.html') or p.endswith('people'):
        return 'members','members'
    if t.strip() == 'members of mifp':
        return 'members','members'

    # ── Scientific Council (singleton) ──
    if 'mifp-scientific-council' in p:
        return 'members','scientific_council'
    if t.strip() == 'scientific council':
        return 'members','scientific_council'

    # ── Manifesto (singleton) ──
    if 'manifesto' in p:
        return 'manifesto','policy'

    # ── Events ──
    if any(k in p for k in ['events-meetings','events-schools',
                             'events-workshops','events-conferences',
                             '/meetings/','/schools/','/workshops/',
                             '/conferences/']):
        return 'events','event_page'
    # title-based events fallback
    if any(k in t for k in ['meeting','workshop','conference','school']):
        # but only if the title seems to describe an actual event page
        if any(w in t for w in ['mifp','international','annual','summer','winter']):
            return 'events','event_page'

    # ── Research / Publications ──
    if any(k in p for k in ['research-mifp-publications', 'research-mifp-pubblications',
                             '/research/publications/', '/research/pubblications/']):
        return 'publications','publications'
    if 'research-projects' in p:
        return 'research','projects'
    if 'publication' in p or 'pubblication' in p:
        return 'publications','publications'
    if 'research' in p:
        return 'research','research'

    # ── Join ──
    if 'join-us' in p or 'become-a-member' in p:
        return 'join','join_members'

    # ── News ──
    if any(k in p for k in ['news','blog','latest-news']):
        return 'news','news'
    # Check for "News e Forthcoming events" page specifically (title + text)
    if ('forthcoming events' in title.lower() or 'forthcoming events' in text.lower()):
        return 'news','news'

    return 'legacy','page'


# ── D. extract_members() with validation ────────────────────────────
_LOCATION_WORDS = {
    'university','institute','department','faculty','lab','laboratory',
    'college','school','academy','center','centre','hospital','clinic',
    'observatory','research','technology','science','sciences',
}

_COUNTRY_RE = re.compile(
    r'\b(Afghanistan|Albania|Algeria|Andorra|Angola|Argentina|Armenia|Australia'
    r'|Austria|Azerbaijan|Bahamas|Bahrain|Bangladesh|Barbados|Belarus|Belgium'
    r'|Belize|Benin|Bhutan|Bolivia|Bosnia|Botswana|Brazil|Brunei|Bulgaria'
    r'|Burkina|Burundi|Cambodia|Cameroon|Canada|Cape Verde|Chad|Chile|China'
    r'|Colombia|Comoros|Congo|Costa Rica|Croatia|Cuba|Cyprus|Czech|Denmark'
    r'|Djibouti|Dominica|Ecuador|Egypt|El Salvador|Eritrea|Estonia|Ethiopia'
    r'|Fiji|Finland|France|Gabon|Gambia|Georgia|Germany|Ghana|Greece|Grenada'
    r'|Guatemala|Guinea|Guyana|Haiti|Honduras|Hungary|Iceland|India|Indonesia'
    r'|Iran|Iraq|Ireland|Israel|Italy|Jamaica|Japan|Jordan|Kazakhstan|Kenya'
    r'|Kiribati|Korea|Kosovo|Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Lesotho'
    r'|Liberia|Libya|Liechtenstein|Lithuania|Luxembourg|Macedonia|Madagascar'
    r'|Malawi|Malaysia|Maldives|Mali|Malta|Marshall|Mauritania|Mauritius'
    r'|Mexico|Micronesia|Moldova|Monaco|Mongolia|Montenegro|Morocco|Mozambique'
    r'|Myanmar|Namibia|Nauru|Nepal|Netherlands|New Zealand|Nicaragua|Niger'
    r'|Nigeria|Norway|Oman|Pakistan|Palau|Palestine|Panama|Papua|Paraguay|Peru'
    r'|Philippines|Poland|Portugal|Qatar|Romania|Russia|Rwanda|Saint|Samoa'
    r'|San Marino|Sao Tome|Saudi|Senegal|Serbia|Seychelles|Sierra Leone'
    r'|Singapore|Slovakia|Slovenia|Solomon|Somalia|South Africa|Spain|Sri Lanka'
    r'|Sudan|Suriname|Swaziland|Sweden|Switzerland|Syria|Taiwan|Tajikistan'
    r'|Tanzania|Thailand|Togo|Tonga|Trinidad|Tunisia|Turkey|Turkmenistan'
    r'|Tuvalu|Uganda|Ukraine|United Arab|United Kingdom|United States|Uruguay'
    r'|Uzbekistan|Vanuatu|Vatican|Venezuela|Vietnam|Yemen|Zambia|Zimbabwe)\b',
    re.I)


def _looks_like_person_name(name):
    """Return True if name looks like a real person name."""
    parts = name.split()
    if len(parts) < 2:
        return False
    if len(name) > 120:
        return False
    # Reject "City Country" pattern (e.g. "Rome Italy", "Hangzhou China")
    if _CITY_COUNTRY_RE.match(name):
        return False
    # Reject if last word is a known country
    if _COUNTRY_RE.search(parts[-1]):
        # Likely "City Country" — not a person
        if len(parts) == 2:
            return False
    # Each word should start with uppercase (standard name format)
    if not all(p[0].isupper() for p in parts if p):
        return False
    # Reject if looks like an affiliation (contains location keywords)
    low = name.lower()
    if any(w in low for w in _LOCATION_WORDS):
        return False
    return True


def extract_members(soup, source_url=None):
    """Extract members only from the real members list page, with validation."""
    out = []
    # Only attempt table extraction from the main content area
    main_el = None
    for sel in ['.item-page', '.blog', '.content', '.component',
                '#component', 'main', 'article']:
        found = soup.select_one(sel)
        if found:
            main_el = found
            break
    search_area = main_el if main_el else soup

    for tr in search_area.select('tr'):
        cells = [clean(c.get_text(' ')) for c in tr.find_all(['td','th'])]
        cells = [c for c in cells if c]
        if len(cells) >= 2 and not any(c.lower() in {'#','member','affiliation'} for c in cells[:2]):
            if cells[0].isdigit() and len(cells) >= 3:
                name, aff = cells[1], cells[2]
            else:
                name, aff = cells[0], cells[1]
            if _looks_like_person_name(name):
                out.append({'type':'member','display_name':name,'affiliation':aff,'source':'local_table'})

    if out:
        return out

    # Fallback: text parsing from main content only
    text_raw = search_area.get_text('\n')
    for line in text_raw.splitlines():
        line = clean(line)
        m = re.match(r'^(\d+)\s+(.+?)\s{2,}(.+)$', line)
        if m:
            name, aff = m.group(2), m.group(3)
            if _looks_like_person_name(name):
                out.append({'type':'member','display_name':name,'affiliation':aff,'source':'local_text'})
    return out


# ── C. print_classification_report() ───────────────────────────────
def print_classification_report(pages):
    """Print diagnostic info about classification results."""
    from collections import Counter
    counts = Counter(p['configured_section'] for p in pages)
    log.info("\n=== Classification Report ===")
    for sec, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        log.info(f"  {sec}: {cnt} pages")

    # Singleton pages that should only appear once
    singletons = ['code_of_conduct','privacy','manifesto']
    for s in singletons:
        sp = [p for p in pages if p['configured_section'] == s]
        if len(sp) > 1:
            log.warning(f"\n  WARNING: '{s}' has {len(sp)} pages (expected ~1):")
            for p in sp[:5]:
                log.warning(f"    - {p['url']}")

    # Show first 20 pages classified as code_of_conduct
    coc = [p for p in pages if p['configured_section'] == 'code_of_conduct']
    if coc:
        log.info(f"\n  code_of_conduct pages ({len(coc)}):")
        for p in coc[:20]:
            log.info(f"    - {p['url']}")

    # Show first 20 pages classified as members
    mem = [p for p in pages if p['configured_section'] == 'members']
    if mem:
        log.info(f"\n  members pages ({len(mem)}):")
        for p in mem[:20]:
            log.info(f"    - {p['url']}")

    # Warn about singleton pages from non-canonical URLs
    canonical = {
        'code_of_conduct': 'mifp-code-of-conduct',
        'privacy': 'privacy-policy',
        'manifesto': 'manifesto',
        'members': ('mifp-members','people.html','people'),
        'scientific_council': 'mifp-scientific-council',
    }
    for sec, canon in canonical.items():
        sp = [p for p in pages if p['configured_section'] == sec]
        for p in sp:
            plow = p['path'].lower()
            if isinstance(canon, tuple):
                if not any(c in plow for c in canon):
                    log.warning(f"  WARNING: singleton '{sec}' from non-canonical URL: {p['url']}")
            else:
                if canon not in plow:
                    log.warning(f"  WARNING: singleton '{sec}' from non-canonical URL: {p['url']}")
    log.info("=== End Report ===\n")


# ── existing helpers kept as-is ─────────────────────────────────────
def page_url(root, file, base_url):
    rel=file.relative_to(root).as_posix()
    if rel.endswith('index.html'): rel=rel[:-10]
    return urljoin(base_url.rstrip('/')+'/', rel)


def _local_file_for_url(url: str, root: Path) -> Path | None:
    """Resolve a site URL to a file inside the local Joomla dump, if present."""
    parsed = urlparse(url)
    if parsed.scheme in {'http', 'https'} and parsed.netloc.lower() not in LOCAL_MIFP_HOSTS:
        return None
    raw_path = unquote(parsed.path or '').lstrip('/')
    if not raw_path:
        return None
    candidates = [root / raw_path]
    for host_dir in ('www.mifp.eu', 'old.mifp.eu', 'www.old.mifp.eu'):
        if not raw_path.startswith(host_dir + '/'):
            candidates.append(root / host_dir / raw_path)
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    return None


def _valid_local_asset(path: Path) -> bool:
    try:
        head = path.read_bytes()[:512].lstrip().lower()
    except OSError:
        return False
    if not head or head.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return False
    extension = path.suffix.casefold()
    if extension == ".pdf" and not head.startswith(b"%pdf"):
        return False
    return True


def resolve_document_root(value: str | Path) -> Path:
    """Accept either a direct document root or its mirror parent."""
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Local mirror root does not exist: {root}")
    candidates = [root / "www.mifp.eu", root / "old.mifp.eu", root / "www.old.mifp.eu"]
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.rglob("*.html")):
            return root
    if any(root.rglob("*.html")):
        return root
    raise ValueError(f"No HTML pages found below local mirror root: {root}")


def _add_local_path(rec: dict, url: str, root: Path, out_dir: Path) -> bool:
    """Copy a local dump asset to assets_downloaded/ and set local_path.

    The local scraper must not cause later pipeline stages to fetch missing
    assets from old.mifp.eu. Returning False lets callers drop asset records
    that cannot be resolved inside the local dump.
    """
    local_file = _local_file_for_url(url, root)
    if local_file is not None and local_file.is_file() and _valid_local_asset(local_file):
        kind = rec.get('kind', 'other')
        local_rel = f"assets_downloaded/{kind}/{local_file.name}"
        dst = out_dir / local_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_file, dst)
        rec['local_path'] = local_rel
        return True
    return False

def asset_kind(url):
    ext=Path(url.split('?',1)[0]).suffix.lower()
    if ext in {'.jpg','.jpeg','.png','.gif','.webp','.svg'}: return 'image'
    if ext=='.pdf': return 'pdf'
    if ext in {'.doc','.docx','.xls','.xlsx','.ppt','.pptx','.csv','.txt'}: return 'document'
    if ext in {'.mp4','.mov'}: return 'video'
    return 'other'


# ── main() ──────────────────────────────────────────────────────────
def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
    ap=argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--output', default='output/raw/local')
    ap.add_argument('--base-url', default='https://old.mifp.eu/')
    args=ap.parse_args()
    root=resolve_document_root(args.root); out=Path(args.output).resolve(); out.mkdir(parents=True, exist_ok=True); (out/'pages_text').mkdir(exist_ok=True)
    pages=[]; assets_by_page=[]; assets_unique={}; members=[]; _event_candidates=[]
    files=[p for p in root.rglob('*') if p.is_file() and (p.suffix.lower() in HTML_EXT or p.name.lower() in {'index'})]
    for order,p in enumerate(sorted(files),1):
        try: html=p.read_text('utf-8',errors='ignore')
        except Exception: continue
        soup=BeautifulSoup(html,'lxml')

        title=clean((soup.title.string if soup.title else '') or '')
        h1=clean(soup.find('h1').get_text(' ') if soup.find('h1') else '')

        # Use extract_main_content() instead of soup.get_text('\n')
        text_clean, lines = extract_main_content(soup, source_url=None)

        url=page_url(root,p,args.base_url)
        section,kind=classify(str(p.relative_to(root)), title+' '+h1, text_clean)

        links=[]; page_assets=[]
        for a in soup.find_all('a',href=True):
            href=urljoin(url,a['href']); label=clean(a.get_text(' ')); links.append({'url':href,'text':label})
            ext=Path(href.split('?',1)[0]).suffix.lower()
            if ext in ASSET_EXT:
                rec={'url':href,'download_url':href,'kind':asset_kind(href),'extension':ext,'labels':label,'first_page_order':order,'first_page_url':url,'used_on_pages_count':1}
                if _add_local_path(rec, href, root, out):
                    page_assets.append(rec); assets_unique.setdefault(href,rec)
        for img in soup.find_all('img',src=True):
            src=urljoin(url,img['src']); ext=Path(src.split('?',1)[0]).suffix.lower()
            rec={'url':src,'download_url':src,'kind':'image','extension':ext,'labels':clean(img.get('alt') or ''),'first_page_order':order,'first_page_url':url,'used_on_pages_count':1}
            if _add_local_path(rec, src, root, out):
                page_assets.append(rec); assets_unique.setdefault(src,rec)

        # Inline PDF URLs from raw HTML — catches JS, data-attr, onclick URLs
        for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf', html, re.IGNORECASE):
            href = urljoin(url, m.group(0).split('?',1)[0].split('#',1)[0])
            if href not in assets_unique:
                rec={'url':href,'download_url':href,'kind':'pdf','extension':'.pdf','labels':'','first_page_order':order,'first_page_url':url,'used_on_pages_count':1}
                if _add_local_path(rec, href, root, out):
                    page_assets.append(rec); assets_unique.setdefault(href,rec)

        rec={'order':order,'depth':0,'source_group':'local_joomla','configured_section':section,'configured_kind':kind,'configured_title':h1 or title,'url':url,'final_url':url,'domain':args.base_url.split('/')[2] if '://' in args.base_url else 'local','path':'/'+p.relative_to(root).as_posix(),'event_site_key':'','status_code':200,'content_type':'text/html','elapsed_seconds':0,'bytes':p.stat().st_size,'title':title or h1 or p.stem,'h1':h1,'headings':[clean(h.get_text(' ')) for h in soup.find_all(re.compile('^h[1-4]$'))],'text':text_clean,'text_lines':lines,'text_sha1':sha1(text_clean),'links':links,'assets':page_assets,'asset_count':len(page_assets),'link_count':len(links),'event_metadata':{},'text_file':f'pages_text/{order:05d}_{p.stem}.txt'}
        pages.append(rec); (out/rec['text_file']).write_text(text_clean,encoding='utf-8')
        for a in page_assets:
            x=dict(a); x['page_url']=url; x['page_order']=order; assets_by_page.append(x)
        if section=='members':
            for m in extract_members(soup, source_url=url):
                m['source_url']=url; members.append(m)
        if section=='events' and kind=='event_page':
            t=h1 or title
            if t and len(text_clean)>200:
                _event_candidates.append({'page_url':url,'title_name':t,'text':text_clean,'order':order})

    # Group event candidates by directory (one event per event site)
    groups = {}
    for c in _event_candidates:
        key = _event_group_key(c['page_url'])
        groups.setdefault(key, []).append(c)

    events = []
    for key, group in groups.items():
        home = min(group, key=lambda c: len(urlparse(c['page_url']).path.strip('/').split('/')))
        merged_text = '\n---\n'.join(c['text'] for c in group if c['text'])
        events.append({
            'event_site_key': sha1(key)[:12],
            'home_url': home['page_url'],
            'title_name': home['title_name'],
            'place': '',
            'start_date': '',
            'end_date': '',
            'date_raw': '',
            'logo_url': '',
            'logo_candidates': '',
            'confidence': 0.35,
            'pages_in_site': len(group),
            'page_orders': ','.join(str(c['order']) for c in sorted(group, key=lambda x: x['order'])),
            'text': merged_text[:10000],
        })

    log.info("  [events] %d page candidates -> %d grouped events", len(_event_candidates), len(events))

    # Extract news from Joomla home-page modules
    news_records = []
    home_index = root / 'www.mifp.eu' / 'index.html'
    if not home_index.exists():
        home_index = root / 'index.html'
    if home_index.exists():
        try:
            home_html = home_index.read_text('utf-8', errors='ignore')
            home_soup = BeautifulSoup(home_html, 'lxml')
            home_url = page_url(root, home_index, args.base_url)
            news_records = extract_joomla_home_news(home_soup, home_url, root=root, out_dir=out)
            log.info(f"  [news] Extracted {len(news_records)} news records from Joomla home page")
        except Exception as e:
            log.error(f"  [news] Error reading home page: {e}")

    def write_jsonl(name, rows):
        with (out/name).open('w',encoding='utf-8') as f:
            for r in rows: f.write(json.dumps(r,ensure_ascii=False)+'\n')
    write_jsonl('pages_all.jsonl',pages); write_jsonl('pages_summary_local_joomla.jsonl',[p for p in pages if p['source_group']=='local_joomla']); write_jsonl('pages_old_mifp.jsonl',[p for p in pages if p['source_group']=='local_joomla']); write_jsonl('assets_by_page.jsonl',assets_by_page); write_jsonl('assets_unique.jsonl',list(assets_unique.values())); write_jsonl('members.jsonl',members); write_jsonl('events_summary_local.jsonl',events); write_jsonl('news.jsonl',news_records); write_jsonl('errors.jsonl',[])

    # Print classification diagnostics
    print_classification_report(pages)
    print(json.dumps({'pages':len(pages),'members':len(members),'event_candidates':len(events),'assets':len(assets_unique),'output':str(out)},indent=2))  # final output

if __name__=='__main__': main()
