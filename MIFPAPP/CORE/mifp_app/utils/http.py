"""Small HTTP helpers shared by routes and application hooks."""
from __future__ import annotations

from urllib.parse import urlsplit

from flask import request


def wants_json_response() -> bool:
    """Return whether the current request explicitly prefers a JSON response."""
    if request.path.startswith("/api") or request.path.endswith(".json"):
        return True
    if request.args.get("format") == "json":
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    best = request.accept_mimetypes.best
    return best == "application/json" and (
        request.accept_mimetypes["application/json"]
        >= request.accept_mimetypes["text/html"]
    )


def is_safe_relative_url(value: str) -> bool:
    """Allow only same-site absolute-path redirects (``/path``)."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return False
    if "\\" in value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "" and parsed.netloc == ""
