"""Safe, explicit registry for public-facing interface copy.

The dashboard may edit only the keys declared here. Values remain plain text:
templates keep autoescaping enabled and no template or HTML source is editable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CopyField:
    key: str
    label: str
    default: str
    group: str
    context: str
    max_length: int = 140
    multiline: bool = False
    setting_key: str | None = None

    @property
    def storage_key(self) -> str:
        return self.setting_key or f"copy.{self.key}"


GROUPS = (
    ("identity", "Identity and homepage", "Main messages visitors see first."),
    ("navigation", "Navigation", "Labels shared by the public navigation and footer."),
    ("content", "Content pages", "Titles, introductions, searches and empty states."),
    ("participation", "Participation", "Joining and sponsor calls to action."),
)


FIELDS = (
    CopyField("hero_eyebrow", "Homepage eyebrow", "Mediterranean Institute", "identity",
              "Small label above the homepage title.", 80, setting_key="hero_eyebrow"),
    CopyField("hero_lead", "Homepage introduction",
              "MIFP advances fundamental science through research, dialogue and lasting collaboration across the Mediterranean.",
              "identity", "Lead paragraph in the homepage hero.", 320, True, "hero_lead"),
    CopyField("home_primary_cta", "Primary homepage action", "Explore events", "identity",
              "Main homepage button.", 48),
    CopyField("home_secondary_cta", "Secondary homepage action", "Discover MIFP", "identity",
              "Secondary homepage button.", 48),
    CopyField("home_news_link", "News feature link", "Read full story", "identity",
              "Link shown on the featured news item.", 48),
    CopyField("home_motto_latin", "Motto", "Dubium sapientiae initium. Memento audere semper.", "identity",
              "Short motto in the homepage statement.", 80),
    CopyField("home_motto_translation", "Motto explanation", "The beginning of wisdom is doubt. Remember always to dare.",
              "identity", "Explanation below the motto.", 160),
    CopyField("events_section_eyebrow", "Events section eyebrow", "What's coming", "identity",
              "Homepage events section label.", 64, setting_key="events_eyebrow"),
    CopyField("events_section_title", "Events section title", "Forthcoming Events", "identity",
              "Homepage events section heading.", 100, setting_key="events_section_title"),
    CopyField("events_section_subtitle", "Events section description",
              "Conferences, workshops, and schools coming up — across the Mediterranean and beyond.", "identity",
              "Homepage events section supporting text.", 220, True,
              "events_section_subtitle"),
    CopyField("news_section_eyebrow", "News section eyebrow", "From the newsroom", "identity",
              "Homepage news section label.", 64, setting_key="news_eyebrow"),
    CopyField("news_section_title", "News section title", "Latest News", "identity",
              "Homepage news section heading.", 100, setting_key="news_section_title"),
    CopyField("news_section_subtitle", "News section description",
              "Updates from the institute, member institutions, and the wider Mediterranean physics community.", "identity",
              "Homepage news section supporting text.", 220, True,
              "news_section_subtitle"),

    CopyField("nav_home", "Home", "Home", "navigation", "Public navigation label.", 32),
    CopyField("nav_events", "Events", "Events", "navigation", "Public navigation label.", 32),
    CopyField("nav_news", "News", "News", "navigation", "Public navigation label.", 32),
    CopyField("nav_members", "Members", "Members", "navigation", "Public navigation label.", 32),
    CopyField("nav_join", "Join us", "Join MIFP", "navigation", "Public navigation label.", 32),
    CopyField("nav_institutional", "Institutional", "Institutional", "navigation",
              "Public navigation group label.", 40),
    CopyField("nav_about", "About MIFP", "About Us", "navigation", "Public navigation label.", 40),
    CopyField("nav_manifesto", "Manifesto", "Manifesto", "navigation", "Public navigation label.", 40),
    CopyField("nav_code", "Code of conduct", "Code of Conduct", "navigation",
              "Public navigation label.", 40),
    CopyField("nav_sponsors", "Become a sponsor", "Become a Sponsor", "navigation", "Public navigation label.", 40),
    CopyField("nav_research", "Research areas", "Research", "navigation",
              "Public navigation label.", 40),
    CopyField("nav_publications", "Publications", "Publications", "navigation",
              "Public navigation label.", 40),
    CopyField("nav_privacy", "Privacy", "Privacy", "navigation", "Footer navigation label.", 40),
    CopyField("nav_cookies", "Cookie policy", "Cookie Policy", "navigation",
              "Footer navigation label.", 40),
    CopyField("nav_admin", "Admin area", "Admin", "navigation",
              "Footer administration link.", 40),
    CopyField("privacy_contact_email", "Privacy contact", "privacy@mifp.eu", "navigation",
              "Contact address used by privacy information.", 160,
              setting_key="privacy_contact_email"),

    CopyField("members_title", "Members title", "Our Members", "content",
              "Members page heading.", 80),
    CopyField("members_intro", "Members introduction",
              "Researchers, scientists, and collaborators who drive the mission of the Mediterranean Institute of Fundamental Physics.", "content",
              "Members page introductory sentence.", 240, True),
    CopyField("members_search", "Members search hint", "Search by name, affiliation, country...",
              "content", "Members search placeholder.", 100),
    CopyField("members_empty", "Members empty state", "No members found. Try adjusting your search or filter criteria.",
              "content", "Shown when no member matches.", 160),
    CopyField("news_title", "News title", "News", "content", "News page heading.", 80),
    CopyField("news_intro", "News introduction",
              "Activities, announcements, publications and institutional updates from MIFP.", "content",
              "News page introductory sentence.", 240, True),
    CopyField("news_search", "News search hint", "Search title or text", "content",
              "News search placeholder.", 100),
    CopyField("news_empty", "News empty state", "No news published yet.", "content",
              "Shown when the list is empty.", 160),
    CopyField("events_title", "Events title", "Events", "content", "Events page heading.", 80),
    CopyField("events_intro", "Events introduction",
              "Conferences, workshops, schools and meetings organized by the Mediterranean Institute of Fundamental Physics.",
              "content", "Events page introductory sentence.", 240, True),
    CopyField("events_upcoming", "Upcoming events heading", "Forthcoming", "content",
              "Heading above future events.", 80),
    CopyField("events_past", "Past events heading", "Past Events", "content",
              "Heading above archived events.", 80),
    CopyField("events_empty", "Events empty state", "No events published yet.",
              "content", "Shown when the list is empty.", 160),
    CopyField("publications_title", "Publications title", "Scientific Publications", "content",
              "Publications page heading.", 80),
    CopyField("publications_intro", "Publications introduction",
              "Peer-reviewed research produced by MIFP members and collaborators across fundamental physics, material science, and interdisciplinary fields.", "content",
              "Publications page introductory sentence.", 240, True),
    CopyField("publications_search", "Publications search hint", "Filter by title, author, journal...",
              "content", "Publications search placeholder.", 100),
    CopyField("publications_empty", "Publications empty state", "Publications coming soon.",
              "content", "Shown when the list is empty.", 160),
    CopyField("research_title", "Research areas title", "Research Areas", "content",
              "Research page heading.", 80),
    CopyField("research_intro", "Research areas introduction",
              "MIFP fosters interdisciplinary research across fundamental physics and related fields.", "content",
              "Research page introductory sentence.", 240, True),
    CopyField("research_pdf", "Research PDF action", "Open PDF", "content",
              "Label for the research overview PDF.", 64),
    CopyField("research_empty", "Research areas empty state",
              "Research areas content coming soon.", "content",
              "Shown when the list is empty.", 160),

    CopyField("sponsors_title", "Sponsors title", "Our Sponsors", "participation",
              "Sponsors page heading.", 80),
    CopyField("sponsors_intro", "Sponsors introduction",
              "MIFP is grateful to the following organizations for their support and commitment to fundamental physics research.",
              "participation", "Sponsors page introductory sentence.", 240, True),
    CopyField("sponsors_empty", "Sponsors empty state", "No sponsors currently listed. Check back soon.",
              "participation", "Shown when the sponsor list is empty.", 160),
    CopyField("sponsors_cta_title", "Sponsor call to action", "Support MIFP", "participation",
              "Sponsor invitation heading.", 100),
    CopyField("sponsors_cta_button", "Sponsor contact action", "Become a sponsor", "participation",
              "Sponsor invitation button.", 48),
    CopyField("join_title", "Join page title", "Membership Request", "participation",
              "Membership request page heading.", 80),
    CopyField("join_intro", "Join page introduction",
              "Become part of an international network of researchers advancing fundamental physics across the Mediterranean and beyond.",
              "participation", "Membership request introductory sentence.", 240, True),
    CopyField("join_submit", "Join form action", "Submit request", "participation",
              "Membership request submit button.", 48),
    CopyField("join_success_title", "Join confirmation title", "Request received",
              "participation", "Heading after a successful request.", 100),
    CopyField("join_success_text",
              "Join confirmation message",
              "Your request has been submitted and will be reviewed internally. You will be contacted when the membership process moves forward.",
              "participation", "Message after a successful request.", 240, True),
)

_BY_STORAGE_KEY = {field.storage_key: field for field in FIELDS}


def copy_setting_keys() -> set[str]:
    return set(_BY_STORAGE_KEY)


def copy_values(settings: Mapping[str, object]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in FIELDS:
        stored = settings.get(field.storage_key)
        value = str(stored).strip() if stored is not None else ""
        values[field.key] = value or field.default
    return values


def copy_groups(settings: Mapping[str, object]) -> list[dict[str, Any]]:
    groups = []
    for group_key, title, description in GROUPS:
        fields = []
        for field in FIELDS:
            if field.group != group_key:
                continue
            stored = settings.get(field.storage_key)
            raw_value = str(stored) if stored is not None else ""
            fields.append({
                "key": field.key,
                "storage_key": field.storage_key,
                "label": field.label,
                "default": field.default,
                "value": raw_value,
                "effective_value": raw_value.strip() or field.default,
                "context": field.context,
                "max_length": field.max_length,
                "multiline": field.multiline,
                "is_custom": bool(raw_value.strip()),
            })
        groups.append({
            "key": group_key,
            "title": title,
            "description": description,
            "fields": fields,
        })
    return groups


def validate_copy_value(storage_key: str, value: object) -> tuple[str | None, str | None]:
    field = _BY_STORAGE_KEY.get(storage_key)
    if field is None:
        return None, "Unknown text setting."
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        return None, f"{field.label} contains unsupported control characters."
    if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", text):
        return None, f"{field.label} must contain plain text, not HTML."
    if len(text) > field.max_length:
        return None, f"{field.label} must be at most {field.max_length} characters."
    return text, None
