#!/usr/bin/env python3
"""Normalizer: reads legacy scraper JSONL and produces standardized JSONL.

Usage:
    python -m build_database_pkg.normalizer \\
        --input-dir output/local_jsonl \\
        --input-dir output/remote_jsonl \\
        --input-dir output/remote_jsonl/aruba_remote \\
        --output-dir output/standard
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import re
from collections import OrderedDict
from datetime import date, datetime as _dt, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

ALLOWED_EVENT_TYPES = {'conference', 'workshop', 'seminar', 'meeting', 'school', 'other'}
ALLOWED_STATUSES = {'published', 'draft', 'review', 'needs_review', 'quarantined', 'duplicate'}
ALLOWED_EVENT_STATUSES = {'draft', 'upcoming', 'past'}
ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str:
    if not text:
        return ''
    return ' '.join(str(text).split())


def _slugify(text: str) -> str:
    text = _clean(text).lower()
    text = re.sub(r'[^\w\s-]', '', text)
    return re.sub(r'[-\s]+', '-', text).strip('-')


def _norm_key(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', _clean(text).lower())


def _decode_html(text: str) -> str:
    import html as _html
    if not text:
        return ''
    return _html.unescape(text)


def _iter_legacy_records(input_dirs: list[Path], glob_pattern: str):
    seen = set()
    for input_dir in input_dirs:
        for path in sorted(input_dir.glob(glob_pattern)):
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue


def _dedup_by_key(records, key_fn):
    seen = set()
    for r in records:
        k = key_fn(r)
        if k and k not in seen:
            seen.add(k)
            yield r


def _dedup_by_best(records, key_fn, score_fn):
    best = {}
    for r in records:
        k = key_fn(r)
        if k:
            if k not in best or score_fn(r) > score_fn(best[k]):
                best[k] = r
    return best.values()


def _news_score(record):
    score = 0
    if record.get('date') or record.get('published_date'):
        score += 100
    body_len = len(_clean(record.get('body') or record.get('text') or record.get('content') or ''))
    score += min(body_len // 100, 50)
    score += len(record.get('images') or []) * 10
    score += len(record.get('links') or []) * 5
    score += len(record.get('documents') or []) * 5
    return score


def _member_score(record):
    score = 0
    if record.get('role') or record.get('position'):
        score += 100
    if record.get('affiliation'):
        score += 20
    if record.get('email') or record.get('contact'):
        score += 10
    bio_len = len(_clean(record.get('bio') or record.get('biography') or record.get('description') or ''))
    score += min(bio_len // 100, 20)
    if record.get('image') or record.get('photo') or record.get('avatar'):
        score += 10
    return score


# ---------------------------------------------------------------------------
# Event normalizer
# ---------------------------------------------------------------------------

def _extract_iso_date(raw: str) -> str:
    """Best effort: try ISO first, then parse human-readable dates."""
    raw = _decode_html(str(raw or '')).strip()
    if ISO_DATE_RE.match(raw):
        return raw
    # "31 October 2022" or "31 Oct 2022"
    m = re.search(
        r'\b(\d{1,2})\s+(' + '|'.join(MONTH_MAP) + r')\s+(20\d{2})\b',
        raw, re.IGNORECASE
    )
    if m:
        day, month_name, year = m.group(1), m.group(2).lower(), m.group(3)
        month = MONTH_MAP[month_name]
        max_day = calendar.monthrange(int(year), month)[1]
        if 1 <= int(day) <= max_day:
            return f'{year}-{month:02d}-{int(day):02d}'
    # "October 2022"
    m = re.search(
        r'\b(' + '|'.join(MONTH_MAP) + r')\s+(20\d{2})\b',
        raw, re.IGNORECASE
    )
    if m:
        month_name, year = m.group(1).lower(), m.group(2)
        return f'{year}-{MONTH_MAP[month_name]:02d}-01'
    # "2022"
    m = re.search(r'\b(20\d{2})\b', raw)
    if m:
        return f'{m.group(1)}-01-01'
    return ''


def _safe_date(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ''


def _strip_ordinal_suffixes(text: str) -> str:
    return re.sub(r'\b(\d{1,2})\s*(st|nd|rd|th)\b', r'\1', text, flags=re.IGNORECASE)


def _extract_event_date_range(raw: str) -> tuple[str, str, str]:
    text = _decode_html(str(raw or ''))
    text = _strip_ordinal_suffixes(text)
    text = re.sub(r'\b(st|nd|rd|th)\b\s*-\s*', '-', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text.replace('–', '-').replace('—', '-')).strip()
    month_names = '|'.join(MONTH_MAP)

    patterns: list[tuple[str, str]] = [
        (
            rf'\b(?P<d1>\d{{1,2}})\s*-\s*(?P<d2>\d{{1,2}})\s+(?:of\s+)?(?P<m1>{month_names}),?\s*(?P<y>20\d{{2}}|19\d{{2}})\b',
            'same_month',
        ),
        (
            rf'\b(?P<m1>{month_names})\s+(?P<d1>\d{{1,2}})\s*-\s*(?P<d2>\d{{1,2}})\s*[,.]?\s*(?P<y>20\d{{2}}|19\d{{2}})\b',
            'same_month',
        ),
        (
            rf'\b(?P<m1>{month_names})\s+(?P<d1>\d{{1,2}})\s*-\s*(?P<m2>{month_names})\s+(?P<d2>\d{{1,2}}),?\s*(?P<y>20\d{{2}}|19\d{{2}})\b',
            'cross_month',
        ),
        (
            rf'\bfrom\s+(?P<d1>\d{{1,2}})(?:\s+of)?\s+(?P<m1>{month_names})\s+to\s+(?P<d2>\d{{1,2}})(?:\s+of)?\s+(?P<m2>{month_names}),?\s*(?P<y>20\d{{2}}|19\d{{2}})\b',
            'cross_month',
        ),
        (
            rf'\bfrom\s+(?P<d1>\d{{1,2}})\s+to\s+(?P<d2>\d{{1,2}})\s+(?:of\s+)?(?P<m1>{month_names})\s+(?P<y>20\d{{2}}|19\d{{2}})\b',
            'same_month',
        ),
    ]

    for pattern, _kind in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        gd = m.groupdict()
        year = int(gd['y'])
        month1 = MONTH_MAP.get(gd['m1'].lower())
        month2 = MONTH_MAP.get((gd.get('m2') or gd['m1']).lower())
        if not month1 or not month2:
            continue
        start = _safe_date(year, month1, int(gd['d1']))
        end = _safe_date(year, month2, int(gd['d2']))
        if start and end:
            return start, end, m.group(0)

    # Some pages split an ordinal marker away from its day in navigation text,
    # leaving only "-13 April 2025". Prefer the visible event month/year over
    # an unrelated single date from a deadline table.
    m = re.search(
        rf'(?:^|\s)-\s*(?P<d2>\d{{1,2}})\s+(?:of\s+)?(?P<m1>{month_names}),?\s*(?P<y>20\d{{2}}|19\d{{2}})\b',
        text,
        re.IGNORECASE,
    )
    if m:
        year = int(m.group('y'))
        month = MONTH_MAP.get(m.group('m1').lower())
        if month:
            one = _safe_date(year, month, int(m.group('d2')))
            if one:
                return one, one, m.group(0).strip()
    return '', '', ''


def _event_status(start_date: str, end_date: str) -> str:
    event_end = end_date or start_date
    if ISO_DATE_RE.match(event_end):
        return 'upcoming' if event_end >= date.today().isoformat() else 'past'
    return 'draft'


def normalize_events(input_dirs: list[Path]) -> list[dict]:
    records = list(_iter_legacy_records(input_dirs, 'events_summary*.jsonl'))
    deduped = _dedup_by_key(records, lambda r: _slugify(r.get('title') or r.get('title_name') or ''))
    out = []
    for r in deduped:
        title = _clean(r.get('title') or r.get('title_name') or '')
        if not title or len(title) < 3:
            continue

        description = _clean(r.get('description') or r.get('body') or r.get('summary') or r.get('text') or '')
        date_raw = _clean(r.get('date_raw') or r.get('date_text') or r.get('date') or '')
        start_date = _extract_iso_date(r.get('start_date') or r.get('date') or '')
        end_date = _extract_iso_date(r.get('end_date') or '')
        parsed_start, parsed_end, parsed_raw = _extract_event_date_range(
            ' '.join([
                date_raw,
                _clean(r.get('start_date') or ''),
                _clean(r.get('end_date') or ''),
                description,
                title,
            ])
        )
        if parsed_start and parsed_end and (not start_date or not end_date or start_date == end_date or parsed_start != start_date):
            start_date = parsed_start
            end_date = parsed_end
            date_raw = parsed_raw or date_raw

        event_type = _clean(r.get('event_type', 'other')).lower()
        if event_type not in ALLOWED_EVENT_TYPES:
            event_type = 'other'

        review_status = _clean(r.get('review_status', 'published')).lower()
        if review_status not in ALLOWED_STATUSES:
            review_status = 'published'

        event_status = _clean(r.get('status') or '').lower()
        if event_status not in ALLOWED_EVENT_STATUSES:
            event_status = _event_status(start_date, end_date)

        location = _clean(r.get('location') or r.get('place') or '')
        url = _clean(r.get('url') or r.get('link') or r.get('external_link') or r.get('home_url') or '')
        cover_url = _clean(r.get('cover_url') or r.get('logo_url') or '')
        date_precision = 'range' if start_date and end_date and start_date != end_date else ('day' if start_date else 'unknown')

        documents = []
        for a in (r.get('assets') or []):
            if isinstance(a, dict):
                doc_url = _clean(a.get('url') or a.get('download_url') or '')
                if doc_url:
                    documents.append({
                        'label': _clean(a.get('label', 'Document')),
                        'url': doc_url,
                    })

        tags = [str(t) for t in (r.get('tags') or []) if t]

        confidence = r.get('confidence')
        out.append(OrderedDict([
            ('type', 'event'),
            ('title', title),
            ('date', start_date),
            ('end_date', end_date),
            ('date_text', date_raw or parsed_raw or start_date),
            ('date_precision', date_precision),
            ('sort_date', start_date or end_date),
            ('event_type', event_type),
            ('review_status', review_status),
            ('status', event_status),
            ('is_published', 1 if (description or url or location or start_date) else 0),
            ('is_featured', 1 if event_status == 'upcoming' else 0),
            ('description', description),
            ('location', location),
            ('url', url),
            ('cover_url', cover_url),
            ('documents', documents),
            ('tags', tags),
            ('confidence', confidence),
        ]))
    return out


# ---------------------------------------------------------------------------
# News normalizer
# ---------------------------------------------------------------------------

def normalize_news(input_dirs: list[Path]) -> list[dict]:
    records = list(_iter_legacy_records(input_dirs, 'news*.jsonl'))
    deduped = _dedup_by_best(records, lambda r: _slugify(r.get('title') or ''), _news_score)
    out = []
    for r in deduped:
        title = _clean(r.get('title') or '')
        if not title or len(title) < 2:
            continue

        body = _clean(r.get('body') or r.get('text') or r.get('content') or '')
        summary = _clean(r.get('summary') or '')

        raw_date = r.get('date') or r.get('published_date') or ''
        date_val = _extract_iso_date(raw_date)

        status = _clean(r.get('review_status', 'published')).lower()
        if status not in ALLOWED_STATUSES:
            status = 'published'

        url = _clean(r.get('url') or r.get('link') or r.get('external_link') or '')

        # Preserve full images list from source for add_news processing
        images_out = []
        for img in (r.get('images') or []):
            if isinstance(img, dict) and img.get('url'):
                item = {
                    'url': _clean(img['url']),
                    'alt_text': _clean(img.get('alt_text', '')),
                    'caption': _clean(img.get('caption', '')),
                    'role': img.get('role', 'cover' if len(images_out) == 0 else 'gallery'),
                    'sort_order': int(img.get('sort_order', len(images_out))),
                }
                if img.get('local_path'):
                    item['local_path'] = str(img.get('local_path'))
                if img.get('download_url'):
                    item['download_url'] = _clean(img.get('download_url'))
                images_out.append(item)
        # Fallback: single image URL
        image = _clean(r.get('image') or '')
        if not image and images_out:
            image = images_out[0]['url']
        if image and not images_out:
            images_out.append({'url': image, 'role': 'cover', 'sort_order': 0})

        documents = []
        for d in (r.get('documents') or []):
            if isinstance(d, dict):
                doc_url = _clean(d.get('url') or '')
                if doc_url:
                    item = {
                        'label': _clean(d.get('label', 'Document')),
                        'url': doc_url,
                    }
                    if d.get('local_path'):
                        item['local_path'] = str(d.get('local_path'))
                    if d.get('download_url'):
                        item['download_url'] = _clean(d.get('download_url'))
                    documents.append(item)
        links = []
        for l in (r.get('links') or []):
            if isinstance(l, dict):
                link_url = _clean(l.get('url') or '')
                if link_url:
                    links.append({
                        'label': _clean(l.get('label', 'Link')),
                        'url': link_url,
                    })

        tags = [str(t) for t in (r.get('tags') or []) if t]
        is_published = r.get('is_published', 1)
        if isinstance(is_published, str):
            is_published = 1 if is_published.lower() in ('1', 'true', 'yes') else 0

        out.append(OrderedDict([
            ('type', 'news'),
            ('source', _clean(r.get('source') or '')),
            ('source_url', _clean(r.get('source_url') or '')),
            ('canonical_url', _clean(r.get('canonical_url') or '')),
            ('scraped_at', _clean(r.get('scraped_at') or '')),
            ('scraper_version', _clean(r.get('scraper_version') or '')),
            ('title', title),
            ('date', date_val),
            ('date_text', _clean(r.get('date_text') or '')),
            ('date_precision', _clean(r.get('date_precision') or 'unknown') or 'unknown'),
            ('date_is_inferred', r.get('date_is_inferred', 0)),
            ('date_inference_rule', r.get('date_inference_rule') or ''),
            ('original_date_text', _clean(r.get('original_date_text') or '')),
            ('review_status', status),
            ('is_published', is_published),
            ('summary', summary),
            ('body', body),
            ('sort_order', int(r.get('sort_order', 0))),
            ('url', url),
            ('image', image),
            ('images', images_out),
            ('documents', documents),
            ('links', links),
            ('tags', tags),
            ('extraction_warnings', r.get('extraction_warnings') or []),
            ('quality_flags_json', r.get('quality_flags_json') or ''),
        ]))

    out = _distribute_news_dates(out)
    return out


def _distribute_news_dates(records):
    """Fill missing news dates by interpolating between known dates
    based on their sort_order position (page appearance order)."""
    if not records:
        return records

    max_remote_so = max(
        (int(r.get('sort_order', 0)) for r in records if 'aruba' in r.get('source', '') or 'remote' in r.get('source', '')),
        default=0,
    )
    for r in records:
        source = r.get('source', '')
        so = int(r.get('sort_order', 0))
        if 'local' in source:
            so += max(1000, max_remote_so + 100)
        r['_sort_pos'] = so

    records.sort(key=lambda r: r['_sort_pos'], reverse=True)
    dated_positions = [(i, r) for i, r in enumerate(records) if r.get('date')]

    if not dated_positions:
        for r in records:
            r.pop('_sort_pos', None)
        records.sort(key=lambda r: int(r.get('sort_order', 0)))
        return records

    for i, r in enumerate(records):
        if r.get('date'):
            continue

        before = None
        after = None
        for di, dr in dated_positions:
            if di < i:
                before = (di, dr)
            elif di > i:
                after = (di, dr)
                break

        if before and after:
            b_date = _dt.strptime(before[1]['date'], '%Y-%m-%d')
            a_date = _dt.strptime(after[1]['date'], '%Y-%m-%d')
            total_gap = max((a_date - b_date).days, 1)
            pos_ratio = (i - before[0]) / max(after[0] - before[0], 1)
            new_date = b_date + timedelta(days=int(total_gap * pos_ratio))
        elif before:
            b_date = _dt.strptime(before[1]['date'], '%Y-%m-%d')
            dist_from_last = i - before[0]
            new_date = b_date + timedelta(days=dist_from_last * 30)
        elif after:
            a_date = _dt.strptime(after[1]['date'], '%Y-%m-%d')
            dist_from_first = after[0] - i
            new_date = a_date - timedelta(days=dist_from_first * 30)

        r['date'] = new_date.strftime('%Y-%m-%d')
        r['date_is_inferred'] = 1
        r['date_inference_rule'] = 'interpolated_sort_order'
        r['date_precision'] = 'day'

    records.sort(key=lambda r: r['_sort_pos'])
    for r in records:
        r.pop('_sort_pos', None)

    return records


# ---------------------------------------------------------------------------
# Member normalizer
# ---------------------------------------------------------------------------


def normalize_members(input_dirs: list[Path]) -> list[dict]:
    records = list(_iter_legacy_records(input_dirs, 'members.jsonl'))
    deduped = _dedup_by_best(records, lambda r: _norm_key(
        r.get('display_name') or r.get('name') or r.get('full_name') or ''
    ), _member_score)
    out = []
    for r in deduped:
        full_name = _clean(r.get('display_name') or r.get('name') or r.get('full_name') or '')
        if not full_name or len(full_name) < 5:
            continue

        status = _clean(r.get('review_status', 'published')).lower()
        if status not in ALLOWED_STATUSES:
            status = 'published'

        email = _clean(r.get('email') or r.get('contact') or '')
        affiliation = _clean(r.get('affiliation') or '')
        country = _clean(r.get('country') or '')

        role = _clean(r.get('role') or r.get('position') or '')
        bio = _clean(r.get('bio') or r.get('biography') or r.get('description') or '')
        image = _clean(r.get('image') or r.get('photo') or r.get('avatar') or '')

        # Split name
        parts = full_name.split()
        first_name = parts[0] if parts else ''
        last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''

        out.append(OrderedDict([
            ('type', 'member'),
            ('name', full_name),
            ('first_name', first_name),
            ('last_name', last_name),
            ('email', email),
            ('role', role),
            ('affiliation', affiliation),
            ('country', country),
            ('review_status', status),
            ('bio', bio),
            ('image', image),
        ]))
    return out


# ---------------------------------------------------------------------------
# Sponsor normalizer
# ---------------------------------------------------------------------------

def normalize_sponsors(input_dirs: list[Path]) -> list[dict]:
    records = list(_iter_legacy_records(input_dirs, 'sponsors.jsonl'))
    deduped = _dedup_by_key(records, lambda r: _slugify(r.get('name') or r.get('sponsor_name') or ''))
    out = []
    for r in deduped:
        name = _clean(r.get('name') or r.get('sponsor_name') or '')
        if not name:
            continue

        description = _clean(r.get('description') or r.get('short_description') or '')
        website = _clean(r.get('website') or r.get('url') or r.get('website_url') or '')
        tier = _clean(r.get('tier', '')).lower()

        # Logo: try logo_url, logo, logoURL, then first image
        image = _clean(r.get('logo_url') or r.get('logo') or r.get('logoURL') or '')
        if not image:
            images = r.get('images') or []
            if images and isinstance(images[0], dict):
                image = _clean(images[0].get('url') or images[0].get('src') or images[0].get('image') or '')

        out.append(OrderedDict([
            ('type', 'sponsor'),
            ('name', name),
            ('description', description),
            ('website', website),
            ('tier', tier),
            ('image', image),
        ]))
    return out


# ---------------------------------------------------------------------------
# Publication normalizer (extracted from pages)
# ---------------------------------------------------------------------------

def normalize_publications(input_dirs: list[Path]) -> list[dict]:
    records = list(_iter_legacy_records(input_dirs, 'pages_all.jsonl'))
    out = []
    for r in records:
        section = str(r.get('configured_section') or '').lower()
        kind = str(r.get('configured_kind') or '').lower()
        url = _clean(r.get('url') or r.get('link') or r.get('source_url') or '')
        lowered_url = url.lower()
        if '/feed/' in lowered_url or 'privacy-policy' in lowered_url:
            continue
        if section != 'publications' and kind != 'publications' and '/publications/' not in url and '/mifp-publications/' not in url:
            continue

        text = _clean(r.get('text') or r.get('body') or r.get('content') or '')

        # Extract title from "File: ... Uploaded:" pattern or URL stem
        title_match = re.search(r'\bFile:\s*(.+?)\s+Uploaded:', text or '', re.IGNORECASE)
        if title_match:
            title = _clean(title_match.group(1).replace('Folder Path:', ''))
        else:
            stem = url.rsplit('/', 1)[-1].rsplit('.', 1)[0]
            stem = re.sub(r'^\d+[-_]+', '', stem)
            title = _clean(stem.replace('-', ' ').title())

        if not title or title.lower() in {'mifp publications', "members' publications", 'publications', 'atom', 'rss', 'privacy policy'}:
            continue

        # Extract authors and abstract
        authors_text = re.sub(r'^.*?\bDownloads:\s*\d+\s*', '', text or '', flags=re.IGNORECASE)
        authors_text = _clean(authors_text)
        authors = ''
        abstract = ''
        if authors_text:
            marker = re.search(r'\s+(The|This|We|Here|Our|In|A|An|It)\s+', authors_text)
            if marker:
                authors = _clean(authors_text[:marker.start()].rstrip('.,;:'))
                abstract = _clean(authors_text[marker.start():])
            else:
                authors = _clean(authors_text[:240].rstrip('.,;:'))
                abstract = _clean(authors_text[240:])

        # Extract year
        year_match = re.search(r'\bUploaded:\s*(\d{2})\.(\d{2})\.(\d{2,4})\b', text or '', re.IGNORECASE)
        year = None
        if year_match:
            year_val = int(year_match.group(3))
            if year_val < 100:
                year_val = 2000 + year_val if year_val < 50 else 1900 + year_val
            year = year_val
        if not year:
            ym = re.search(r'\b(19|20)\d{2}\b', text or '')
            if ym:
                year = int(ym.group(0))

        # PDF URL from assets
        pdf_url = ''
        for a in (r.get('assets') or []):
            if isinstance(a, dict):
                asset_url = _clean(a.get('download_url') or a.get('url') or '')
                kind_lower = _clean(a.get('kind') or a.get('extension') or '').lower()
                if asset_url.lower().split('?', 1)[0].endswith('.pdf') or 'pdf' in kind_lower:
                    pdf_url = asset_url.split('?', 1)[0].split('#', 1)[0]
                    break

        # Fallback: search in text/body/content for inline PDF URLs
        if not pdf_url:
            haystack = ' '.join(filter(None, [
                r.get('text'), r.get('body'), r.get('content'),
                r.get('display_link'), r.get('external_link'),
            ]))
            inline = re.search(r'https?://[^\s"\'<>]+\.pdf', haystack, re.IGNORECASE)
            if inline:
                pdf_url = inline.group(0).split('?', 1)[0].split('#', 1)[0]

        out.append(OrderedDict([
            ('type', 'publication'),
            ('title', title),
            ('year', year),
            ('authors', authors),
            ('abstract', abstract),
            ('url', url),
            ('pdf_url', pdf_url),
        ]))
    return out


# ---------------------------------------------------------------------------
# Research area normalizer (extracted from the Research page)
# ---------------------------------------------------------------------------

RESEARCH_AREA_DEFS = [
    ('Solid State Physics and Astrophysics', 'Solid State Physics and Astrophysics'),
    ('Particle Physics and Gaseous Detectors', 'gaseous detectors for elementary particles'),
    ('Light-Matter Coupling and Nanostructures', 'light-matter coupling in nanostructures'),
    ('Superconductivity and Quantum Transport', 'superconductivity'),
    ('Photovoltaics and Semiconductor Materials', 'organic solar cells'),
    ('Quantum Optics and Cavity Electrodynamics', 'quantum cavity electrodynamics'),
]


def normalize_research_areas(input_dirs: list[Path]) -> list[dict]:
    # Find the Research page in all pages
    research_text = ''
    for r in _iter_legacy_records(input_dirs, 'pages_all.jsonl'):
        section = str(r.get('configured_section') or '').lower()
        title = str(r.get('configured_title') or r.get('title') or '').strip().lower()
        url = _clean(r.get('url') or r.get('link') or r.get('source_url') or '')
        if section == 'research' and (title == 'research' or url.endswith('/research.html')):
            research_text = _clean(r.get('text') or r.get('body') or r.get('content') or '')
            break

    if not research_text:
        return []

    out = []
    lower_text = research_text.lower()
    for sort_order, (area_title, needle) in enumerate(RESEARCH_AREA_DEFS, start=1):
        if needle.lower() not in lower_text:
            continue
        pos = lower_text.find(needle.lower())
        summary = _clean(research_text[max(0, pos - 160):pos + 360])

        out.append(OrderedDict([
            ('type', 'research_area'),
            ('title', area_title),
            ('summary', summary[:260]),
            ('description', summary[:1000]),
            ('sort_order', sort_order),
        ]))
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ENTITY_NORMALIZERS = [
    ('events', 'events.jsonl', normalize_events),
    ('news', 'news.jsonl', normalize_news),
    ('members', 'members.jsonl', normalize_members),
    ('sponsors', 'sponsors.jsonl', normalize_sponsors),
    ('publications', 'publications.jsonl', normalize_publications),
    ('research_areas', 'research_areas.jsonl', normalize_research_areas),
]


def normalize_all(input_dirs: list[Path], output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, filename, normalizer_fn in ENTITY_NORMALIZERS:
        records = normalizer_fn(input_dirs)
        filepath = output_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        counts[name] = len(records)
        log.info(f"  {name}: {len(records)} records -> {filepath.relative_to(output_dir.parent)}")
    asset_count = carry_asset_cache(input_dirs, output_dir)
    if asset_count:
        log.info(f"  assets: {asset_count} cached records -> {(output_dir / 'assets_unique.jsonl').relative_to(output_dir.parent)}")
    return counts


def carry_asset_cache(input_dirs: list[Path], output_dir: Path) -> int:
    """Preserve scraper asset cache metadata for the DB builder.

    The standard JSONL files are intentionally entity-focused, but the builder
    also needs assets_unique.jsonl local_path entries to read assets from the
    local Joomla mirror instead of trying old public URLs.
    """
    seen_urls = set()
    records = []
    for input_dir in input_dirs:
        asset_file = input_dir / 'assets_unique.jsonl'
        if not asset_file.exists():
            continue
        with asset_file.open('r', encoding='utf-8') as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = record.get('download_url') or record.get('url')
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                local_path = record.get('local_path')
                if local_path:
                    asset_path = input_dir / str(local_path)
                    if asset_path.is_file():
                        record = dict(record)
                        record['local_path'] = os.path.relpath(asset_path, output_dir)
                records.append(record)

    output_file = output_dir / 'assets_unique.jsonl'
    with output_file.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    return len(records)


def main():
    parser = argparse.ArgumentParser(description='Normalize legacy scraper JSONL to standard format')
    parser.add_argument('--input-dir', action='append', required=True,
                        help='Legacy JSONL directory (can be specified multiple times)')
    parser.add_argument('--output-dir', default='output/standard',
                        help='Output directory for standardized JSONL (default: output/standard)')
    args = parser.parse_args()

    input_dirs = [Path(d) for d in args.input_dir]
    output_dir = Path(args.output_dir)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
    log.info("Normalizing legacy JSONL to standard format...")
    log.info(f"  Inputs: {', '.join(str(d) for d in input_dirs)}")
    log.info(f"  Output: {output_dir}")
    counts = normalize_all(input_dirs, output_dir)
    total = sum(counts.values())
    log.info(f"Done: {total} total records written to {output_dir}")


if __name__ == '__main__':
    main()
