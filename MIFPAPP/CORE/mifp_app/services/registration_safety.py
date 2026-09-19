"""Safety rules for public scaffolding inside ``regform/registrations``.

Conference Editor packages may include a *directory skeleton* that protects the
runtime registration storage (for example ``.gitignore``, a deny-only
``.htaccess`` and a tiny 404 ``index.php``).  Those files are public source
scaffolding, not submitted registration data.  Everything else below
``regform/registrations`` remains private-by-default and must never be imported
into the public events tree.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

_REGISTRATION_ROOT = ("regform", "registrations")
_PLACEHOLDER_NAMES = {".gitkeep", ".keep"}
_MAX_GUARD_BYTES = 16 * 1024
_INDEX_404_RE = re.compile(
    rb"^\s*<\?php\s+http_response_code\s*\(\s*404\s*\)\s*;\s*exit\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_ALLOWED_HTACCESS_LINE = re.compile(
    r"^(?:"
    r"#.*|"
    r"<IfModule\s+mod_rewrite\.c>|"
    r"</IfModule>|"
    r"RewriteEngine\s+On|"
    r"RewriteRule\s+\^\s+-\s+\[F,L\]|"
    r"Require\s+all\s+denied|"
    r"Order\s+allow,deny|"
    r"Deny\s+from\s+all"
    r")$",
    re.IGNORECASE,
)


def is_registration_path(path: PurePosixPath) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    return len(parts) >= 2 and parts[:2] == _REGISTRATION_ROOT


def is_safe_public_registration_scaffold(path: PurePosixPath, payload: bytes) -> bool:
    """Return whether a registration-tree file is known public guard scaffolding.

    This intentionally uses a very small allow-list.  A CSV, database, uploaded
    proof, arbitrary PHP file or any other runtime submission remains rejected.
    """
    if not is_registration_path(path) or len(payload) > _MAX_GUARD_BYTES:
        return False

    relative = PurePosixPath(*path.parts[2:])
    name = relative.name.casefold()

    if name in _PLACEHOLDER_NAMES:
        return len(payload) <= 256 and b"\x00" not in payload

    if relative.as_posix().casefold() == ".gitignore":
        try:
            payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            return False
        return b"\x00" not in payload

    if relative.as_posix().casefold() == "index.php":
        return bool(_INDEX_404_RE.fullmatch(payload))

    if relative.as_posix().casefold() == ".htaccess":
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            return False
        meaningful = [line.strip() for line in text.splitlines() if line.strip()]
        return bool(meaningful) and all(_ALLOWED_HTACCESS_LINE.fullmatch(line) for line in meaningful)

    return False
