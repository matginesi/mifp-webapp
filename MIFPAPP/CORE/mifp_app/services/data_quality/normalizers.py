from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_DOI = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
_PAGE = re.compile(r"\b(?:page\s+\d+\s+of\s+\d+|results\s+\d+\s*[-–]\s*\d+\s+of\s+\d+)\b", re.I)
_TECH_LINE = re.compile(
    r"^\s*(?:start|prev|next|files?|download|folder path\s*:?.*|file\s*:?.*|"
    r"uploaded\s*:?.*|modified\s*:?.*|file size\s*:?.*|accept cookies?|"
    r"enable javascript|authorization required|404 view not found.*)\s*$",
    re.I,
)
_AGGREGATE_MARKERS = re.compile(
    r"(?:start\s+prev\s+next\s+files?|folder path\s*:|uploaded\s*:|file size\s*:|"
    r"page\s+\d+\s+of\s+\d+|results\s+\d+\s*[-–]\s*\d+\s+of\s+\d+)",
    re.I,
)
_ARCHIVE_PATH = re.compile(r"(?:archive|publications[0-9a-f]{4,}|page/\d+|[?&]page=\d+)", re.I)
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_ORG_SUFFIX = re.compile(r"\s+(?:Ltd|Inc|Corp|S\.p\.A\.|GmbH|S\.?A\.?R\.?L\.?|LLC|PLC|Co\..*)$", re.I)
_DIACRITICS = re.compile(r"[^a-zA-Z0-9\s&]+")
_AMP = re.compile(r"\band\b", re.I)
_PARTICLES = frozenset({"di", "de", "del", "della", "van", "von", "da", "der", "den", "ter", "vom", "zum"})
_SURNAME_SUFFIX = re.compile(r"(ov|ev|in|sky|ski|ic|ich|son|sen|wicz|ez|off|ian|yan)$", re.I)


def comparison_text(value: object) -> str:
    raw = html.unescape(str(value or ""))
    raw = unicodedata.normalize("NFKC", raw).casefold()
    raw = raw.replace("’", "'").replace("–", "-").replace("—", "-")
    return _SPACE.sub(" ", _PUNCT.sub(" ", raw)).strip()


def tokens(value: object) -> tuple[str, ...]:
    return tuple(part for part in comparison_text(value).split() if part)


def normalized_doi(value: object) -> str:
    return _DOI.sub("", comparison_text(value)).strip()


def stable_fingerprint(entity_type: str, records: list[dict], *, action: str = "") -> str:
    operational = {
        "id", "uid", "created_at", "updated_at", "sort_order", "source_order", "display_order"
    }
    material = [
        {
            key: value
            for key, value in sorted(row.items())
            if key not in operational
        }
        for row in sorted(
            records,
            key=lambda item: json.dumps(
                {key: value for key, value in item.items() if key not in operational},
                sort_keys=True,
                default=str,
            ),
        )
    ]
    return hashlib.sha256(
        json.dumps([entity_type, action, material], ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def content_fingerprint(record: dict) -> str:
    exclude = {"id", "uid", "slug", "sort_order", "source_order",
               "display_order", "created_at", "updated_at"}
    clean = {k: v for k, v in record.items() if k not in exclude}
    raw = json.dumps(clean, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def clean_boilerplate(value: object) -> tuple[str, list[str]]:
    text = html.unescape(str(value or "")).replace("\r\n", "\n")
    removed: list[str] = []
    kept: list[str] = []
    for segment in re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z])", text):
        segment = segment.strip()
        if not segment:
            continue
        if _TECH_LINE.match(segment) or _PAGE.search(segment):
            removed.append(segment)
        else:
            kept.append(segment)
    return "\n\n".join(kept).strip(), removed


def aggregate_markers(value: object) -> list[str]:
    return sorted({match.group(0) for match in _AGGREGATE_MARKERS.finditer(str(value or ""))})


def split_aggregate_segments(value: object) -> list[str]:
    text = str(value or "")
    parts = re.split(
        r"(?i)(?:\bdownload\b|\buploaded\s*:|\bpage\s+\d+\s+of\s+\d+\b|"
        r"\bresults\s+\d+\s*[-–]\s*\d+\s+of\s+\d+\b)",
        text,
    )
    return [part.strip() for part in parts if len(comparison_text(part)) >= 30]


def normalize_url(value: object) -> str:
    raw = unquote(str(value or "").strip())
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold().removeprefix("www.")
    path = re.sub(r"/+", "/", parts.path or "/")
    path = path.replace("/www.mifp.eu/", "/")
    return urlunsplit(("https", host, path.rstrip("/") or "/", "", ""))


def classify_url(value: object) -> str:
    url = normalize_url(value)
    if not url:
        return "generic"
    parts = urlsplit(url)
    path = parts.path.casefold()
    if path.endswith(".pdf") or "download" in path:
        return "document"
    if path in ("", "/"):
        return "site_root"
    if _ARCHIVE_PATH.search(path):
        return "archive"
    if re.search(r"/(?:news|event|member|people|publication|paper|sponsor)/[^/]+", path):
        return "entity_detail"
    return "external_reference"


def normalize_identity_text(value: object) -> str:
    """Normalize an identity string by stripping diacritics, org suffixes, parentheticals, and normalizing case."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = html.unescape(text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("&", " and ")
    text = _AMP.sub("&", text)
    text = _ORG_SUFFIX.sub("", text)
    text = _TRAILING_PAREN.sub("", text)
    text = _DIACRITICS.sub(" ", text)
    return _SPACE.sub(" ", text).strip().lower()


def normalize_person_name(value: object) -> str:
    """Return stable 'last, first' canonical form.

    Uses _SURNAME_SUFFIX heuristic on the first word of 2-word names to detect
    already-inverted input (e.g. "Kavokin Alexey" → "kavokin, alexey").
    This regex matches common Eastern European surname endings
    (ov, ev, in, sky, ic, etc.) and may produce false positives
    for non-Eastern-European names whose first word coincidentally
    matches a suffix pattern (e.g. "Martin Lewis" → "martin, lewis").
    This is an accepted trade-off for MIFP's predominantly Eastern-European
    name corpus.
    """
    p = person_name(value)
    if len(p.normal) < 2:
        return str(value or "").strip().lower()
    surname = p.surname
    given = p.given
    if len(p.normal) == 2:
        first_norm = p.normal[0]
        first_inv = p.inverted[0]
        if first_norm not in _PARTICLES and first_inv not in _PARTICLES:
            if _SURNAME_SUFFIX.search(first_norm):
                surname = first_norm
                given = first_inv
    return surname + ", " + given


def normalize_title(value: object) -> str:
    """Normalize a page title by stripping MIFP/Mediterranean Institute suffixes, pipe fragments, and boilerplate."""
    text = str(value or "").strip()
    text = re.sub(r"\s*[—–|]\s*MIFP.*$", "", text)
    text = re.sub(r"\s*[—–|]\s*Mediterranean Institute.*$", "", text)
    text = re.sub(r"\s*-\s*Home$", "", text, flags=re.I)
    text = re.sub(r"\s*\|.*$", "", text)
    text = re.sub(r"\s*Past Event\s*$", "", text, flags=re.I)
    return comparison_text(text)


def normalize_canonical_url(value: object) -> str:
    """Normalize a URL by stripping tracking params, adding scheme, and normalizing mifp.eu hosts."""
    raw = unquote(str(value or "").strip())
    if not raw:
        return ""
    has_scheme = "://" in raw
    if not has_scheme:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold().replace("old.mifp.eu", "www.mifp.eu").replace("events.mifp.eu", "www.mifp.eu")
    if host == "mifp.eu" and not has_scheme:
        host = "www.mifp.eu"
    path = re.sub(r"/+", "/", parts.path or "/")
    path = re.sub(r"/media/[^/]+/v1/", "/media/", path)
    path = path.replace("/www.mifp.eu/", "/")
    query = parts.query
    if query:
        query = re.sub(r"(?:^|&)(?:utm_[^&=]+|fbclid|gclid|ref|source)=[^&]*", "", query).strip("&")
    return urlunsplit(("https", host, path.rstrip("/") or "/", query, ""))


@dataclass(frozen=True)
class PersonName:
    original: str
    normal: tuple[str, ...]
    inverted: tuple[str, ...]
    initials: tuple[str, ...]
    surname: str = ""
    given: str = ""


def person_name(value: object) -> PersonName:
    original = _SPACE.sub(" ", str(value or "")).strip()
    parts = list(tokens(original))
    inverted = list(parts)
    surname = ""
    given = ""
    if len(parts) > 1:
        last = len(parts) - 1
        while last > 0 and parts[last] in _PARTICLES:
            last -= 1
        if parts[last] not in _PARTICLES:
            sur_start = last
            while sur_start > 0 and parts[sur_start - 1] in _PARTICLES:
                sur_start -= 1
            surname = " ".join(parts[sur_start:])
            given = " ".join(parts[:sur_start])
            inverted = parts[sur_start:] + parts[:sur_start]
        else:
            surname = parts[-1]
            given = " ".join(parts[:-1])
            inverted = parts[-1:] + parts[:-1]
    else:
        surname = parts[0] if parts else ""
        given = ""
    initials = tuple(part[0] for part in parts if part)
    return PersonName(original, tuple(parts), tuple(inverted), initials, surname, given)


def person_names_equivalent(left: object, right: object) -> bool:
    a, b = person_name(left), person_name(right)
    if len(a.normal) < 2 or len(b.normal) < 2:
        return False
    return a.normal == b.normal or a.normal == b.inverted or a.inverted == b.normal


def years(value: object) -> set[int]:
    return {int(match.group(0)) for match in _YEAR.finditer(str(value or ""))}
