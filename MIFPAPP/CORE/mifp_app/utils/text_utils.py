from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

try:
    from slugify import slugify
except Exception:
    import re
    def slugify(value: str) -> str:
        value = str(value).lower()
        value = re.sub(r"[^a-z0-9]+", "-", value)
        return value.strip("-")


def normalize_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or raw.startswith(("#", "/")):
        return None
    if raw.startswith("mailto:"):
        return raw
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return parsed.geturl()
