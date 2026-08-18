from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from build_database_pkg.normalizer import (
    carry_asset_cache,
    normalize_events,
    normalize_news,
    normalize_members,
    normalize_sponsors,
    normalize_publications,
    normalize_research_areas,
)
from scrape_remote import normalize_date_range


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(dir_path: Path, filename: str, records: list[dict]):
    path = dir_path / filename
    with open(path, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    return dir_path


def _write_pages_jsonl(dir_path: Path, records: list[dict]):
    return _write_jsonl(dir_path, 'pages_all.jsonl', records)


@pytest.fixture
def input_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def test_normalize_events_happy_path(input_dir):
    _write_jsonl(input_dir, 'events_summary_001.jsonl', [
        {'title': 'MIFP Conference 2024', 'start_date': '2024-09-15', 'end_date': '2024-09-17',
         'location': 'Rome', 'description': 'Annual conference', 'event_type': 'conference'},
        {'title': 'Workshop', 'date': '2024-10-15', 'location': 'Milan',
         'body': 'A workshop', 'event_type': 'workshop', 'review_status': 'published'},
    ])
    result = normalize_events([input_dir])
    assert len(result) == 2

    conf = result[0]
    assert conf['type'] == 'event'
    assert conf['title'] == 'MIFP Conference 2024'
    assert conf['date'] == '2024-09-15'
    assert conf['end_date'] == '2024-09-17'
    assert conf['event_type'] == 'conference'
    assert conf['review_status'] == 'published'
    assert conf['location'] == 'Rome'

    ws = result[1]
    assert ws['date'] == '2024-10-15'
    assert ws['event_type'] == 'workshop'
    assert ws['review_status'] == 'published'


def test_normalize_events_human_date_to_iso(input_dir):
    _write_jsonl(input_dir, 'events_summary_001.jsonl', [
        {'title': 'Event with human date', 'date': '15 October 2024'},
        {'title': 'Event with month year', 'date': 'October 2024'},
        {'title': 'Event with year only', 'date': '2024'},
        {'title': 'Event with ISO date', 'date': '2024-06-01'},
    ])
    result = normalize_events([input_dir])
    dates = {r['title']: r['date'] for r in result}
    assert dates['Event with human date'] == '2024-10-15'
    assert dates['Event with month year'] == '2024-10-01'
    assert dates['Event with year only'] == '2024-01-01'
    assert dates['Event with ISO date'] == '2024-06-01'


def test_normalize_events_prefers_full_range_in_description(input_dir):
    _write_jsonl(input_dir, 'events_summary_001.jsonl', [
        {
            'title': 'PLMCN 2026',
            'start_date': '2026-01-28',
            'end_date': '2026-01-28',
            'description': 'Yerevan - Armenia, 12 th - 15 April, 2026',
            'confidence': 0.9,
        },
        {
            'title': 'MIFP March Meeting 2024',
            'description': 'The meeting will be held February 27 th - March 2 nd 2024.',
            'confidence': 0.9,
        },
        {
            'title': 'ICP2DC 2024',
            'start_date': '2024-03-14',
            'description': 'Belgrade - Serbia July 2 nd - 6 th , 2024',
            'confidence': 0.9,
        },
        {
            'title': 'PLMCN-2025',
            'start_date': '2024-10-31',
            'description': 'Xiamen - China th -13 April 2025',
            'confidence': 0.9,
        },
    ])
    result = normalize_events([input_dir])
    dates = {r['title']: (r['date'], r['end_date'], r['date_precision']) for r in result}

    assert dates['PLMCN 2026'] == ('2026-04-12', '2026-04-15', 'range')
    assert dates['MIFP March Meeting 2024'] == ('2024-02-27', '2024-03-02', 'range')
    assert dates['ICP2DC 2024'] == ('2024-07-02', '2024-07-06', 'range')
    assert dates['PLMCN-2025'] == ('2025-04-13', '2025-04-13', 'day')


def test_scraper_date_range_handles_spaced_ordinals():
    parsed = normalize_date_range('Yerevan - Armenia, 12 th - 15 April, 2026')
    assert parsed['start_date'] == '2026-04-12'
    assert parsed['end_date'] == '2026-04-15'

    parsed = normalize_date_range('February 27 th - March 2 nd 2024')
    assert parsed['start_date'] == '2024-02-27'
    assert parsed['end_date'] == '2024-03-02'

    parsed = normalize_date_range('Belgrade - Serbia July 2 nd - 6 th , 2024')
    assert parsed['start_date'] == '2024-07-02'
    assert parsed['end_date'] == '2024-07-06'


def test_normalize_events_dedup_by_slug(input_dir):
    _write_jsonl(input_dir, 'events_summary_001.jsonl', [
        {'title': 'MIFP Conference 2024', 'start_date': '2024-09-15'},
        {'title': 'MIFP Conference 2024', 'start_date': '2024-10-01'},
    ])
    result = normalize_events([input_dir])
    assert len(result) == 1


def test_normalize_events_validates_event_type(input_dir):
    _write_jsonl(input_dir, 'events_summary_001.jsonl', [
        {'title': 'Valid', 'event_type': 'seminar'},
        {'title': 'Invalid type', 'event_type': 'party'},
        {'title': 'No type'},
    ])
    result = normalize_events([input_dir])
    types = {r['title']: r['event_type'] for r in result}
    assert types['Valid'] == 'seminar'
    assert types['Invalid type'] == 'other'
    assert types['No type'] == 'other'


def test_normalize_events_skips_short_title(input_dir):
    _write_jsonl(input_dir, 'events_summary_001.jsonl', [
        {'title': 'AB'},
        {'title': 'Valid Event'},
    ])
    result = normalize_events([input_dir])
    assert len(result) == 1
    assert result[0]['title'] == 'Valid Event'


def test_normalize_events_empty_directory(input_dir):
    result = normalize_events([input_dir])
    assert result == []


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

def test_normalize_news_happy_path(input_dir):
    _write_jsonl(input_dir, 'news.jsonl', [
        {'title': 'News Item 1', 'body': 'Body text', 'date': '2024-03-15',
         'review_status': 'published', 'is_published': 1},
        {'title': 'News Item 2', 'body': 'Another body', 'date': '15 March 2024'},
    ])
    result = normalize_news([input_dir])
    assert len(result) == 2
    assert result[0]['date'] == '2024-03-15'
    assert result[0]['review_status'] == 'published'
    assert result[0]['is_published'] == 1
    assert result[1]['date'] == '2024-03-15'


def test_normalize_news_extracts_image_from_images_list(input_dir):
    _write_jsonl(input_dir, 'news.jsonl', [
        {'title': 'With Image', 'body': 'Body',
         'images': [{'url': 'https://example.com/img.jpg'}]},
    ])
    result = normalize_news([input_dir])
    assert result[0]['image'] == 'https://example.com/img.jpg'


def test_normalize_news_extracts_documents_and_links(input_dir):
    _write_jsonl(input_dir, 'news.jsonl', [
        {'title': 'With Assets', 'body': 'Body',
         'documents': [{'label': 'PDF', 'url': 'https://example.com/doc.pdf'}],
         'links': [{'label': 'Source', 'url': 'https://example.com'}]},
    ])
    result = normalize_news([input_dir])
    assert len(result[0]['documents']) == 1
    assert result[0]['documents'][0]['label'] == 'PDF'
    assert len(result[0]['links']) == 1
    assert result[0]['links'][0]['url'] == 'https://example.com'


def test_normalize_news_preserves_aruba_remote_metadata(input_dir):
    _write_jsonl(input_dir, 'news.jsonl', [
        {
            'source': 'aruba_remote_home',
            'source_url': 'https://old.mifp.eu/',
            'title': 'Remote item',
            'body': '',
            'review_status': 'needs_review',
            'is_published': 0,
            'images': [{'url': 'https://example.com/image.jpg'}],
            'quality_flags_json': '{"confidence": 0.55}',
        },
    ])
    result = normalize_news([input_dir])
    assert result[0]['source'] == 'aruba_remote_home'
    assert result[0]['source_url'] == 'https://old.mifp.eu/'
    assert result[0]['review_status'] == 'needs_review'
    assert result[0]['is_published'] == 0
    assert result[0]['quality_flags_json'] == '{"confidence": 0.55}'


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def test_normalize_members_happy_path(input_dir):
    _write_jsonl(input_dir, 'members.jsonl', [
        {'display_name': 'Mario Rossi', 'email': 'mario@example.com',
         'affiliation': 'Sapienza', 'country': 'Italy', 'bio': 'Physicist'},
        {'name': 'John Smith', 'email': 'john@example.com'},
    ])
    result = normalize_members([input_dir])
    assert len(result) == 2
    assert result[0]['name'] == 'Mario Rossi'
    assert result[0]['first_name'] == 'Mario'
    assert result[0]['last_name'] == 'Rossi'
    assert result[0]['email'] == 'mario@example.com'
    assert result[0]['affiliation'] == 'Sapienza'
    assert result[0]['country'] == 'Italy'
    assert result[0]['bio'] == 'Physicist'


def test_normalize_members_dedup(input_dir):
    _write_jsonl(input_dir, 'members.jsonl', [
        {'display_name': 'Mario Rossi', 'email': 'mario@example.com'},
        {'display_name': 'Mario Rossi', 'email': 'mario.other@example.com'},
    ])
    result = normalize_members([input_dir])
    assert len(result) == 1
    assert result[0]['email'] == 'mario@example.com'


def test_normalize_members_skips_short_name(input_dir):
    _write_jsonl(input_dir, 'members.jsonl', [
        {'display_name': 'AB'},
        {'display_name': 'Valid Name'},
    ])
    result = normalize_members([input_dir])
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Sponsors
# ---------------------------------------------------------------------------

def test_normalize_sponsors_happy_path(input_dir):
    _write_jsonl(input_dir, 'sponsors.jsonl', [
        {'name': 'ACME Corp', 'description': 'A company',
         'website': 'https://acme.com', 'tier': 'gold'},
    ])
    result = normalize_sponsors([input_dir])
    assert len(result) == 1
    assert result[0]['name'] == 'ACME Corp'
    assert result[0]['website'] == 'https://acme.com'
    assert result[0]['tier'] == 'gold'


def test_normalize_sponsors_logo_from_multiple_fields(input_dir):
    _write_jsonl(input_dir, 'sponsors.jsonl', [
        {'name': 'Logo from field', 'logo_url': 'https://example.com/logo.png'},
        {'name': 'Logo from images', 'images': [{'url': 'https://example.com/img.png'}]},
        {'name': 'No logo'},
    ])
    result = normalize_sponsors([input_dir])
    assert result[0]['image'] == 'https://example.com/logo.png'
    assert result[1]['image'] == 'https://example.com/img.png'
    assert result[2]['image'] == ''


# ---------------------------------------------------------------------------
# Publications (extracted from pages)
# ---------------------------------------------------------------------------

def test_normalize_publications_from_pages(input_dir):
    _write_pages_jsonl(input_dir, [
        {'configured_section': 'publications', 'url': 'https://example.com/pub1',
         'text': 'Title: Quantum paper\nFile: quantum.pdf Uploaded: 15.06.2024\n'
                 'Downloads: 5\nRossi M., Smith J. '
                 'This paper presents a quantum physics study.',
         'assets': [{'download_url': 'https://example.com/quantum.pdf', 'kind': 'pdf'}]},
    ])
    result = normalize_publications([input_dir])
    assert len(result) == 1
    assert result[0]['title'] == 'quantum.pdf'
    assert 'quantum physics' in result[0]['abstract'].lower()
    assert result[0]['authors'] == 'Rossi M., Smith J'  # rstrip('.,;:') strips trailing period
    assert result[0]['year'] == 2024
    assert result[0]['pdf_url'] == 'https://example.com/quantum.pdf'


def test_normalize_publications_skips_feed_and_privacy(input_dir):
    _write_pages_jsonl(input_dir, [
        {'configured_section': 'publications', 'url': 'https://example.com/feed/atom',
         'text': 'Some text'},
        {'configured_section': 'publications', 'url': 'https://example.com/privacy-policy',
         'text': 'Some text'},
    ])
    result = normalize_publications([input_dir])
    assert result == []


def test_normalize_publications_skips_non_publication_sections(input_dir):
    _write_pages_jsonl(input_dir, [
        {'configured_section': 'news', 'url': 'https://example.com/news1',
         'text': 'Some news'},
    ])
    result = normalize_publications([input_dir])
    assert result == []


# ---------------------------------------------------------------------------
# Research areas (extracted from research page)
# ---------------------------------------------------------------------------

RESEARCH_PAGE_TEXT = (
    "The MIFP research group is organized into several areas. "
    "We study Solid State Physics and Astrophysics with advanced techniques. "
    "Our work on gaseous detectors for elementary particles is world-class. "
    "We investigate light-matter coupling in nanostructures. "
    "Superconductivity and quantum transport phenomena are studied. "
    "We develop organic solar cells for renewable energy. "
    "Quantum cavity electrodynamics is a key research direction."
)


def test_normalize_research_areas_happy_path(input_dir):
    _write_pages_jsonl(input_dir, [
        {'configured_section': 'research', 'title': 'Research',
         'url': 'https://example.com/research.html',
         'text': RESEARCH_PAGE_TEXT},
    ])
    result = normalize_research_areas([input_dir])
    assert len(result) == 6
    assert result[0]['title'] == 'Solid State Physics and Astrophysics'
    assert result[0]['sort_order'] == 1
    assert result[-1]['title'] == 'Quantum Optics and Cavity Electrodynamics'
    assert result[-1]['sort_order'] == 6


def test_normalize_research_areas_no_research_page(input_dir):
    _write_pages_jsonl(input_dir, [
        {'configured_section': 'news', 'title': 'News',
         'text': 'Some news content'},
    ])
    result = normalize_research_areas([input_dir])
    assert result == []


def test_normalize_research_areas_partial_match(input_dir):
    text = (
        "Research areas: Solid State Physics and Astrophysics is one area. "
        "Another area is organic solar cells development."
    )
    _write_pages_jsonl(input_dir, [
        {'configured_section': 'research', 'title': 'Research',
         'url': 'https://example.com/research.html', 'text': text},
    ])
    result = normalize_research_areas([input_dir])
    titles = {r['title'] for r in result}
    assert 'Solid State Physics and Astrophysics' in titles
    assert 'Photovoltaics and Semiconductor Materials' in titles
    # Areas not in text should be omitted
    assert 'Quantum Optics and Cavity Electrodynamics' not in titles


# ---------------------------------------------------------------------------
# Multiple input directories
# ---------------------------------------------------------------------------

def test_multiple_input_dirs_dedup(input_dir):
    dir1 = input_dir / 'dir1'
    dir2 = input_dir / 'dir2'
    dir1.mkdir()
    dir2.mkdir()

    _write_jsonl(dir1, 'members.jsonl', [
        {'display_name': 'Mario Rossi', 'email': 'mario@example.com'},
    ])
    _write_jsonl(dir2, 'members.jsonl', [
        {'display_name': 'Mario Rossi', 'email': 'mario@v2.com'},
        {'display_name': 'Jane Doe', 'email': 'jane@example.com'},
    ])

    result = normalize_members([dir1, dir2])
    assert len(result) == 2  # Mario (dedup) + Jane
    mario = next(r for r in result if r['name'] == 'Mario Rossi')
    assert mario['email'] == 'mario@example.com'  # first dir wins


def test_no_jsonl_files_in_directory(input_dir):
    assert normalize_events([input_dir]) == []
    assert normalize_news([input_dir]) == []
    assert normalize_members([input_dir]) == []
    assert normalize_sponsors([input_dir]) == []
    assert normalize_publications([input_dir]) == []
    assert normalize_research_areas([input_dir]) == []


def test_carry_asset_cache_preserves_local_paths(input_dir, tmp_path):
    asset_dir = input_dir / 'assets_downloaded' / 'image'
    asset_dir.mkdir(parents=True)
    (asset_dir / 'logo.png').write_bytes(b'\x89PNG\r\n\x1a\npayload')
    _write_jsonl(input_dir, 'assets_unique.jsonl', [
        {
            'url': 'https://old.mifp.eu/logo.png',
            'download_url': 'https://old.mifp.eu/logo.png',
            'kind': 'image',
            'local_path': 'assets_downloaded/image/logo.png',
        },
    ])

    output_dir = tmp_path / 'standard'
    output_dir.mkdir()
    count = carry_asset_cache([input_dir], output_dir)

    assert count == 1
    records = [json.loads(line) for line in (output_dir / 'assets_unique.jsonl').read_text().splitlines()]
    assert len(records) == 1
    assert (output_dir / records[0]['local_path']).is_file()
