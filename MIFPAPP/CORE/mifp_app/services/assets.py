from __future__ import annotations

import hashlib
import http.client
import ipaddress
import mimetypes
import shutil
import socket
import tempfile
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import Request

from ..config import Config
from ..db.connection import sha256_file
from ..utils.text_utils import slugify


def infer_kind(path: Path, mime_type: str | None = None) -> str:
    mt = mime_type or mimetypes.guess_type(path.name)[0] or ""
    if mt.startswith("image/"):
        return "image"
    if mt == "application/pdf":
        return "pdf"
    if mt.startswith("video/"):
        return "video"
    if mt in {"application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}:
        return "document"
    return "other"


def infer_kind_from_url(url: str, mime_type: str | None = None) -> str:
    parsed = urlparse(url or "")
    filename = Path(unquote(parsed.path)).name or "remote"
    mt = mime_type or mimetypes.guess_type(filename)[0]
    return infer_kind(Path(filename), mt)


def resolve_db_asset_path(assets_dir: Path, db_path: str | None) -> Path:
    """Resolve an asset.path value without ever scanning arbitrary disk paths.

    `store_asset` stores paths relative to `assets_dir.parent`, usually
    `assets/image/file.png`. Some older rows may store `image/file.png` instead.
    This helper accepts both forms and is used only for DB-tracked paths.
    """
    raw = str(db_path or "").strip()
    if not raw or "\x00" in raw:
        raise ValueError("Invalid asset path")
    root = assets_dir.resolve()
    p = Path(raw)
    if p.is_absolute():
        raise ValueError("Absolute asset paths are not allowed")
    parts = p.parts
    # Canonical DB paths use an abstract ``assets/`` prefix, independent of
    # the configured directory name. Accept the actual directory basename too
    # for rows created by older versions with a custom ASSETS_DIR.
    if parts and parts[0] in {"assets", root.name}:
        p = Path(*parts[1:])
    candidate = (root / p).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Asset path escapes the configured asset directory") from exc
    return candidate


def db_asset_file_is_valid(assets_dir: Path, db_path: str | None, kind: str | None = None, mime_type: str | None = None, filename: str | None = None) -> bool:
    if not db_path:
        return False
    try:
        path = resolve_db_asset_path(assets_dir, db_path)
    except ValueError:
        return False
    if not path.exists() or not path.is_file():
        fallback_name = Path(str(db_path)).name
        path = assets_dir / fallback_name
    if not path.exists() or not path.is_file():
        return False
    return asset_file_is_valid(path, kind=kind, mime_type=mime_type, filename=filename or path.name)


def _filename_from_url(url: str, content_type: str | None = None) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    if name and Path(name).suffix:
        return name
    ext = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip()) or ""
    stem = slugify(Path(name or parsed.netloc or "remote-asset").stem) or "remote-asset"
    return f"{stem}{ext}"


def _download_url_candidates(url: str) -> list[str]:
    parsed = urlparse(url)
    candidates = [url]
    if parsed.netloc in {"old.mifp.eu", "www.old.mifp.eu"}:
        path = parsed.path or ""
        if path.startswith("/www.mifp.eu/"):
            candidates.append(parsed._replace(path=path.removeprefix("/www.mifp.eu")).geturl())
        if "/images/" in path or "/research/" in path or "/ESF/" in path:
            candidates.append(parsed._replace(netloc="www.mifp.eu").geturl())
            candidates.append(parsed._replace(netloc="mifp.eu").geturl())
    seen: set[str] = set()
    out: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _is_blocked_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _host_allowed_by_config(hostname: str) -> bool:
    allowed = Config.ASSET_ALLOWED_DOMAINS
    if not allowed:
        return True
    host = hostname.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed)


def _resolve_allowed_ip(host: str, parsed) -> str:
    """Resolve a validated hostname to a concrete, allowed address.

    Raises if resolution fails or if any resolved address is blocked, so the
    caller can connect to the returned address without a second (rebindable)
    resolution. Returned address must be pinned for the subsequent connection.
    """
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Remote asset host cannot be resolved: {host}") from exc
    if not infos:
        raise ValueError(f"Remote asset host cannot be resolved: {host}")
    for info in infos:
        address = info[4][0]
        if _is_blocked_ip(str(address)):
            raise ValueError(f"Remote asset host resolves to a blocked address: {address}")
    return str(infos[0][4][0])


def _validate_and_resolve(url: str, *, resolve_dns: bool = False) -> tuple[str, str | None]:
    """Validate a remote asset URL and (optionally) resolve it once.

    Returns ``(validated_url, pinned_ip)``. When ``resolve_dns`` is true the
    hostname is resolved exactly once and ``pinned_ip`` is the address the
    caller must connect to, closing the DNS-rebinding window (a later
    re-resolution cannot target a blocked network).
    """
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Unsupported remote asset URL: {raw}")
    if parsed.username or parsed.password:
        raise ValueError("Remote asset URLs cannot contain credentials")
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTS or host.endswith(".local") or _is_blocked_ip(host):
        raise ValueError(f"Remote asset host is not allowed: {host}")
    if not _host_allowed_by_config(host):
        raise ValueError(f"Remote asset host is outside ASSET_ALLOWED_DOMAINS: {host}")
    pinned_ip: str | None = None
    if resolve_dns:
        pinned_ip = _resolve_allowed_ip(host, parsed)
    return parsed.geturl(), pinned_ip


def validate_external_asset_url(url: str, *, resolve_dns: bool = False) -> str:
    """Reject URLs that could target local services or private networks."""
    validated, _ = _validate_and_resolve(url, resolve_dns=resolve_dns)
    return validated


# ---------------------------------------------------------------------------
# Pinned transport: connect to the exact validated IP (SSRF / DNS-rebinding).
# The hostname is still sent in the Host header and TLS SNI, so TLS validation
# and virtual-host routing keep working while the socket is pinned to the IP
# that passed validation. Redirects are re-validated and pinned per hop.
# ---------------------------------------------------------------------------


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, mifp_pinned_ip=None):
        super().__init__(host, timeout=timeout, source_address=source_address)
        self._mifp_pinned_ip = mifp_pinned_ip

    def _create_connection(self, timeout, source_address=None):
        if self._mifp_pinned_ip is not None:
            return socket.create_connection((self._mifp_pinned_ip, self.port), timeout, source_address)
        return super()._create_connection(timeout, source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, context=None, check_hostname=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, mifp_pinned_ip=None):
        if check_hostname is not None and context is not None:
            context.check_hostname = check_hostname
        super().__init__(host, context=context, timeout=timeout, source_address=source_address)
        self._mifp_pinned_ip = mifp_pinned_ip

    def _create_connection(self, timeout, source_address=None):
        if self._mifp_pinned_ip is not None:
            return socket.create_connection((self._mifp_pinned_ip, self.port), timeout, source_address)
        return super()._create_connection(timeout, source_address)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req, mifp_pinned_ip=getattr(req, "_mifp_pinned_ip", None))


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(
            _PinnedHTTPSConnection,
            req,
            context=self._context,
            check_hostname=self._context.check_hostname,
            mifp_pinned_ip=getattr(req, "_mifp_pinned_ip", None),
        )


class _SecureRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only after re-validating and pinning each hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newreq = super().redirect_request(req, fp, code, msg, headers, newurl)
        if newreq is None:
            return None
        try:
            _, pinned_ip = _validate_and_resolve(newreq.full_url, resolve_dns=True)
        except ValueError:
            return None
        newreq._mifp_pinned_ip = pinned_ip
        return newreq


_pinned_opener = urllib.request.build_opener(
    _SecureRedirectHandler(),
    _PinnedHTTPHandler(),
    _PinnedHTTPSHandler(),
)

# Module-level seam: `_download_with_retries` calls `urlopen(req, timeout=...)`.
urlopen = _pinned_opener.open


ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "svg",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip",
    "mp4", "mov", "txt", "csv",
}

_IMAGE_SIGNATURES = (
    b"\xff\xd8\xff",
    b"\x89PNG\r\n\x1a\n",
    b"GIF87a",
    b"GIF89a",
)
_OFFICE_SIGNATURES = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
)
def _looks_like_html(head: bytes) -> bool:
    stripped = head.lstrip().lower()
    return stripped.startswith((b"<!doctype html", b"<html")) or b"<html" in stripped[:512]


def asset_file_is_valid(path: Path, kind: str | None = None, mime_type: str | None = None, filename: str | None = None) -> bool:
    """Validate stored asset bytes against the intended asset kind.

    This intentionally rejects HTML error/landing pages saved behind .jpg/.pdf
    names, which is the common failure mode for the legacy MIFP/Aruba URLs.
    """
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    if not head or _looks_like_html(head):
        return False

    guessed = (mime_type or mimetypes.guess_type(filename or path.name)[0] or "").lower()
    final_kind = (kind or infer_kind(path, guessed)).lower()
    suffix = Path(filename or path.name).suffix.lower()

    if final_kind == "image" or guessed.startswith("image/"):
        return (
            head.startswith(_IMAGE_SIGNATURES)
            or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
            or b"<svg" in head.lstrip().lower()
        )
    if final_kind == "pdf" or guessed == "application/pdf" or suffix == ".pdf":
        return head.startswith(b"%PDF")
    if final_kind == "document":
        if suffix == ".pdf" or guessed == "application/pdf":
            return head.startswith(b"%PDF")
        if suffix in {".zip", ".docx", ".xlsx", ".pptx"} or guessed in {
            "application/zip",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }:
            return head.startswith(_OFFICE_SIGNATURES[:3])
        if suffix in {".doc", ".xls", ".ppt"}:
            return head.startswith(_OFFICE_SIGNATURES)
        return True
    if final_kind == "video":
        return head[4:8] == b"ftyp" or head.startswith(b"RIFF")
    return True


def validate_asset_file(path: Path, kind: str | None = None, mime_type: str | None = None, filename: str | None = None) -> None:
    if not asset_file_is_valid(path, kind=kind, mime_type=mime_type, filename=filename):
        label = filename or path.name
        raise ValueError(f"Downloaded asset is not a valid {kind or 'asset'} file: {label}")


def _normalize_upload_source(source: Any) -> tuple[Path, str | None, bool]:
    if hasattr(source, "save") and hasattr(source, "filename"):
        filename = str(source.filename or "upload.bin")
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext and ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"File extension not allowed: .{ext}")
        suffix = Path(filename).suffix or ".bin"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = Path(tmp.name)
        tmp.close()
        source.save(tmp_path)
        return tmp_path, filename, True
    return Path(source), None, False


def store_asset(conn, source_path: Any, assets_dir: Path, kind: str | None = None, caption: str | None = None, alt_text: str | None = None, source_url: str | None = None, original_filename: str | None = None, *, commit: bool = True) -> int:
    source_path, uploaded_name, is_temp = _normalize_upload_source(source_path)
    source_path = source_path.resolve()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(source_path)
    try:
        source_name = original_filename or uploaded_name or source_path.name
        mime_type = mimetypes.guess_type(source_name)[0] or mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        final_kind = kind or infer_kind(Path(source_name), mime_type)
        validate_asset_file(source_path, kind=final_kind, mime_type=mime_type, filename=source_name)

        checksum = sha256_file(source_path)
        existing = conn.execute("SELECT id FROM assets WHERE checksum = ?", (checksum,)).fetchone()
        if existing:
            return int(existing["id"])

        source_name_path = Path(source_name)
        safe_stem = slugify(source_name_path.stem) or "asset"
        ext = source_name_path.suffix.lower() or source_path.suffix.lower()
        if ext.lower().lstrip(".") not in ALLOWED_EXTENSIONS:
            raise ValueError(f"File extension not allowed: {ext}")
        filename = f"{safe_stem}-{checksum[:12]}{ext}"
        subdir = assets_dir / final_kind
        subdir.mkdir(parents=True, exist_ok=True)
        dest = subdir / filename
        shutil.copy2(source_path, dest)

        rel_path = str(Path("assets") / dest.relative_to(assets_dir))
        cur = conn.execute(
            """
            INSERT INTO assets(
                filename, original_filename, path, mime_type, size, kind, alt_text, caption,
                source_url, checksum, content_sha256, source_url_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename, source_name, rel_path, mime_type, source_path.stat().st_size,
                final_kind, alt_text, caption, source_url, checksum, checksum,
                hashlib.sha256(source_url.encode("utf-8")).hexdigest() if source_url else None,
            ),
        )
        if commit:
            conn.commit()
        return int(cur.lastrowid)
    finally:
        if is_temp:
            try:
                source_path.unlink()
            except OSError:
                pass


def replace_asset_file(
    conn,
    asset_id: int,
    source_path: Path,
    assets_dir: Path,
    kind: str | None = None,
    source_url: str | None = None,
    original_filename: str | None = None,
    mime_type: str | None = None,
    *,
    commit: bool = True,
) -> int:
    source_name = original_filename or source_path.name
    guessed_mime = mime_type or mimetypes.guess_type(source_name)[0] or mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
    final_kind = kind or infer_kind(Path(source_name), guessed_mime)
    validate_asset_file(source_path, kind=final_kind, mime_type=guessed_mime, filename=source_name)
    checksum = sha256_file(source_path)
    duplicate = conn.execute("SELECT id FROM assets WHERE checksum = ? AND id != ?", (checksum, asset_id)).fetchone()
    if duplicate:
        replacement_id = int(duplicate["id"])
        conn.execute("UPDATE asset_links SET asset_id=? WHERE asset_id=?", (replacement_id, asset_id))
        conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        if commit:
            conn.commit()
        return replacement_id

    source_name_path = Path(source_name)
    safe_stem = slugify(source_name_path.stem) or "asset"
    ext = source_name_path.suffix.lower() or source_path.suffix.lower()
    if ext.lower().lstrip(".") not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File extension not allowed: {ext}")
    filename = f"{safe_stem}-{checksum[:12]}{ext}"
    subdir = assets_dir / final_kind
    subdir.mkdir(parents=True, exist_ok=True)
    dest = subdir / filename
    shutil.copy2(source_path, dest)
    rel_path = str(Path("assets") / dest.relative_to(assets_dir))

    conn.execute(
        """
        UPDATE assets
        SET filename=?, original_filename=?, path=?, mime_type=?, size=?, kind=?,
            source_url=COALESCE(?, source_url), checksum=?, content_sha256=?,
            source_url_sha256=CASE WHEN ? IS NOT NULL THEN ? ELSE source_url_sha256 END,
            storage_status='local', is_external=0,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (
            filename,
            source_name,
            rel_path,
            guessed_mime,
            source_path.stat().st_size,
            final_kind,
            source_url,
            checksum,
            checksum,
            source_url,
            hashlib.sha256(source_url.encode("utf-8")).hexdigest() if source_url else None,
            asset_id,
        ),
    )
    if commit:
        conn.commit()
    return asset_id


def _download_with_retries(
    url: str,
    timeout: float,
    max_bytes: int,
    expected_kind: str | None = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> tuple[Path, str, str]:
    """Download a URL with a bounded total attempt count.

    ``max_retries`` is shared by every fallback URL, so adding URL candidates
    never multiplies the request count.
    """
    last_exc: Exception | None = None
    candidates: list[tuple[str, str | None]] = []
    for candidate in _download_url_candidates(url):
        validated, pinned_ip = _validate_and_resolve(candidate, resolve_dns=True)
        candidates.append((validated, pinned_ip))
    attempts = 0
    candidate_index = 0
    while candidates and attempts < max(1, max_retries):
        candidate_url, pinned_ip = candidates[candidate_index % len(candidates)]
        candidate_index += 1
        attempts += 1
        parsed = urlparse(candidate_url)
        for ch in parsed.path:
            if (ch and ord(ch) < 32) or ch == ' ':
                raise ValueError(f"URL path contains invalid characters (spaces/control): {candidate_url}")
        req = Request(candidate_url, headers={
            "User-Agent": Config.HTTP_USER_AGENT,
            "Accept": "image/*,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/zip,video/*,application/octet-stream,*/*;q=0.8",
        })
        if pinned_ip is not None:
            req._mifp_pinned_ip = pinned_ip  # type: ignore[attr-defined]
        tmp_path: Path | None = None
        try:
            with urlopen(req, timeout=timeout) as response:
                status = getattr(response, "status", getattr(response, "code", 200))
                if status in {300, 301, 302, 303, 307, 308}:
                    raise ValueError(f"Remote asset redirect was refused: {candidate_url}")
                content_type = response.headers.get("Content-Type") or mimetypes.guess_type(urlparse(candidate_url).path)[0] or "application/octet-stream"
                declared_length = response.headers.get("Content-Length")
                if declared_length and int(declared_length) > max_bytes:
                    raise ValueError(f"Remote asset too large: {candidate_url} ({declared_length} bytes)")
                filename = _filename_from_url(candidate_url, content_type)
                suffix = Path(filename).suffix or mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ".bin"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp_path = Path(tmp.name)
                    total = 0
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"Remote asset too large: {candidate_url} (> {max_bytes} bytes)")
                        tmp.write(chunk)

            final_kind = expected_kind or infer_kind(Path(filename), content_type)
            validate_asset_file(tmp_path, kind=final_kind, mime_type=content_type, filename=filename)
            return tmp_path, filename, content_type
        except Exception as exc:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            last_exc = exc
            if _is_permanent_download_error(exc):
                candidates = [candidate for candidate in candidates if candidate[0] != candidate_url]
                candidate_index = 0
            if candidates and attempts < max_retries:
                delay = min(base_delay * (2 ** (attempts - 1)), 4.0)
                log = __import__("logging").getLogger("mifp.assets")
                log.warning(
                    "Download attempt %d/%d failed for %s, retrying in %.0fs: %s",
                    attempts, max_retries, candidate_url, delay, exc,
                )
                time.sleep(delay)

    if last_exc is None:
        raise ValueError("No valid remote asset URL candidates")
    raise last_exc


def _is_permanent_download_error(exc: Exception) -> bool:
    if isinstance(exc, ValueError):
        if "cannot be resolved" in str(exc).lower():
            return False
        return True
    if isinstance(exc, HTTPError):
        return exc.code in {400, 401, 403, 404, 405, 410, 413, 415, 422}
    return False


def download_asset(
    conn,
    url: str,
    assets_dir: Path,
    kind: str | None = None,
    caption: str | None = None,
    alt_text: str | None = None,
    timeout: float | None = None,
    max_bytes: int | None = None,
    max_retries: int | None = None,
    *,
    commit: bool = True,
) -> int:
    """Download a remote asset, store it locally, and register it in DB.

    Existing rows with the same source_url are reused before doing network I/O.
    If two different URLs serve the same file, `store_asset` will still dedupe by
    checksum. Generic web pages should normally be stored with `store_external_asset`.
    """
    url = validate_external_asset_url(url)
    timeout = Config.ASSET_DOWNLOAD_TIMEOUT_SECONDS if timeout is None else timeout
    max_retries = Config.ASSET_DOWNLOAD_MAX_ATTEMPTS if max_retries is None else max(1, max_retries)
    max_bytes = max_bytes or Config.ASSET_REMOTE_MAX_BYTES

    existing = conn.execute(
        "SELECT id, path, kind, mime_type, filename FROM assets WHERE source_url = ? ORDER BY id DESC LIMIT 1",
        (url,),
    ).fetchone()
    if existing and db_asset_file_is_valid(
        assets_dir,
        existing["path"],
        kind=existing["kind"],
        mime_type=existing["mime_type"],
        filename=existing["filename"],
    ):
        return int(existing["id"])

    tmp_path, filename, content_type = _download_with_retries(url, timeout, max_bytes, expected_kind=kind, max_retries=max_retries)
    try:
        final_kind = kind or infer_kind(Path(filename), content_type)
        validate_asset_file(tmp_path, kind=final_kind, mime_type=content_type, filename=filename)
        if existing:
            return replace_asset_file(
                conn,
                int(existing["id"]),
                tmp_path,
                assets_dir,
                kind=final_kind,
                source_url=url,
                original_filename=filename,
                mime_type=content_type,
                commit=commit,
            )
        return store_asset(
            conn,
            tmp_path,
            assets_dir,
            kind=final_kind,
            caption=caption,
            alt_text=alt_text,
            source_url=url,
            original_filename=filename,
            commit=commit,
        )
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def store_external_asset(
    conn,
    url: str,
    kind: str = "other",
    caption: str | None = None,
    alt_text: str | None = None,
    *,
    commit: bool = True,
) -> int:
    url = validate_external_asset_url(url)
    existing = conn.execute(
        "SELECT id FROM assets WHERE source_url=? ORDER BY id DESC LIMIT 1",
        (url,),
    ).fetchone()
    if existing:
        return int(existing["id"])
    checksum = hashlib.sha256(url.encode("utf-8")).hexdigest()
    existing = conn.execute("SELECT id FROM assets WHERE checksum = ?", (checksum,)).fetchone()
    if existing:
        return int(existing["id"])

    parsed = urlparse(url)
    has_path_file = bool(Path(parsed.path).name and Path(parsed.path).suffix)
    raw_name = Path(parsed.path).name if has_path_file else (parsed.netloc or "external-link")
    safe_stem = slugify(Path(raw_name).stem) or "external-link"
    ext = Path(raw_name).suffix.lower() if has_path_file else ".url"
    if not ext:
        ext = ".url"
    filename = f"{safe_stem}-{checksum[:12]}{ext}"
    path = f"external/{filename}"
    cur = conn.execute(
        """
        INSERT INTO assets(
            filename, original_filename, path, mime_type, size, kind, alt_text, caption,
            source_url, checksum, source_url_sha256, is_external, storage_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'external')
        """,
        (filename, raw_name, path, "text/uri-list", 0, kind, alt_text, caption or url, url, checksum, checksum),
    )
    if commit:
        conn.commit()
    return int(cur.lastrowid)


def _ensure_recovery_state_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_recovery_state (
            asset_id INTEGER PRIMARY KEY,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            next_attempt_at TEXT,
            last_error TEXT,
            terminal INTEGER NOT NULL DEFAULT 0 CHECK(terminal IN (0,1)),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_asset_recovery_ready
        ON asset_recovery_state(terminal, next_attempt_at, attempts)
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS reset_asset_recovery_after_source_change
        AFTER UPDATE OF source_url ON assets
        WHEN COALESCE(OLD.source_url,'') != COALESCE(NEW.source_url,'')
        BEGIN
            DELETE FROM asset_recovery_state WHERE asset_id=NEW.id;
        END
        """
    )


def asset_recovery_overview(conn) -> dict[str, int]:
    _ensure_recovery_state_schema(conn)
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS missing,
            SUM(CASE WHEN COALESCE(a.source_url,'') != '' THEN 1 ELSE 0 END) AS with_url,
            SUM(CASE WHEN COALESCE(a.source_url,'') = '' THEN 1 ELSE 0 END) AS without_url,
            SUM(CASE WHEN COALESCE(s.terminal,0)=1 THEN 1 ELSE 0 END) AS terminal,
            SUM(CASE WHEN COALESCE(s.terminal,0)=0
                       AND s.next_attempt_at IS NOT NULL
                       AND s.next_attempt_at > CURRENT_TIMESTAMP THEN 1 ELSE 0 END) AS deferred
        FROM assets a
        LEFT JOIN asset_recovery_state s ON s.asset_id=a.id
        WHERE a.storage_status IN ('missing','external')
        """
    ).fetchone()
    return {
        "missing": int(row["missing"] or 0),
        "with_url": int(row["with_url"] or 0),
        "without_url": int(row["without_url"] or 0),
        "terminal": int(row["terminal"] or 0),
        "deferred": int(row["deferred"] or 0),
    }


def _record_recovery_failure(
    conn,
    asset_id: int,
    exc: Exception,
    *,
    max_attempts: int,
    backoff_hours: float,
) -> tuple[int, bool]:
    _ensure_recovery_state_schema(conn)
    current = conn.execute(
        "SELECT attempts FROM asset_recovery_state WHERE asset_id=?",
        (asset_id,),
    ).fetchone()
    attempts = int(current["attempts"] or 0) + 1 if current else 1
    terminal = _is_permanent_download_error(exc) or attempts >= max_attempts
    delay_hours = min(backoff_hours * (2 ** max(0, attempts - 1)), 24 * 7)
    now = datetime.now(UTC)
    next_attempt = None if terminal else now + timedelta(hours=delay_hours)
    conn.execute(
        """
        INSERT INTO asset_recovery_state(
            asset_id, attempts, last_attempt_at, next_attempt_at,
            last_error, terminal, updated_at
        ) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(asset_id) DO UPDATE SET
            attempts=excluded.attempts,
            last_attempt_at=excluded.last_attempt_at,
            next_attempt_at=excluded.next_attempt_at,
            last_error=excluded.last_error,
            terminal=excluded.terminal,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            asset_id,
            attempts,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            next_attempt.strftime("%Y-%m-%d %H:%M:%S") if next_attempt else None,
            str(exc)[:500],
            1 if terminal else 0,
        ),
    )
    conn.execute(
        "UPDATE assets SET storage_status='missing', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (asset_id,),
    )
    return attempts, terminal


def recover_missing_assets(
    conn,
    assets_dir: Path,
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
    max_bytes: int | None = None,
    max_assets: int | None = None,
    max_attempts: int | None = None,
    time_budget: float | None = None,
    backoff_hours: float | None = None,
    force: bool = False,
    statuses: tuple[str, ...] = ("missing", "external", "local"),
) -> dict[str, Any]:
    """Recover a bounded batch and persist retry/cooldown state per asset."""
    log = __import__("logging").getLogger("mifp.assets")
    _ensure_recovery_state_schema(conn)
    timeout = Config.ASSET_DOWNLOAD_TIMEOUT_SECONDS if timeout is None else timeout
    max_retries = Config.ASSET_DOWNLOAD_MAX_ATTEMPTS if max_retries is None else max(1, max_retries)
    max_assets = Config.ASSET_RECOVERY_MAX_ASSETS_PER_RUN if max_assets is None else max(1, max_assets)
    max_attempts = Config.ASSET_RECOVERY_MAX_RUN_ATTEMPTS if max_attempts is None else max(1, max_attempts)
    time_budget = Config.ASSET_RECOVERY_TIME_BUDGET_SECONDS if time_budget is None else max(1.0, time_budget)
    backoff_hours = Config.ASSET_RECOVERY_BACKOFF_HOURS if backoff_hours is None else max(1.0, backoff_hours)
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT a.id, a.path, a.source_url, a.kind, a.caption, a.alt_text,
               a.storage_status, COALESCE(s.attempts,0) AS recovery_attempts,
               COALESCE(s.terminal,0) AS recovery_terminal, s.next_attempt_at
        FROM assets a
        LEFT JOIN asset_recovery_state s ON s.asset_id=a.id
        WHERE a.storage_status IN ({placeholders})
        ORDER BY COALESCE(s.terminal,0), COALESCE(s.attempts,0), a.id DESC
        """,
        statuses,
    ).fetchall()
    result: dict[str, Any] = {
        "total": len(rows),
        "eligible": 0,
        "attempted": 0,
        "recovered": 0,
        "failed": [],
        "marked_missing": 0,
        "skipped": 0,
        "deferred": 0,
        "terminal": 0,
        "no_source": 0,
        "budget_exhausted": False,
    }
    started = time.monotonic()
    now_text = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        asset_id = int(row["id"])
        url = str(row["source_url"] or "").strip()
        if not url:
            result["no_source"] += 1
            continue
        try:
            resolved = resolve_db_asset_path(assets_dir, row["path"])
            file_ok = resolved.exists() and resolved.is_file()
        except ValueError:
            file_ok = False
        if file_ok:
            result["skipped"] += 1
            conn.execute("DELETE FROM asset_recovery_state WHERE asset_id=?", (asset_id,))
            continue
        if not force and bool(row["recovery_terminal"]):
            result["terminal"] += 1
            continue
        next_attempt = str(row["next_attempt_at"] or "")
        if not force and next_attempt and next_attempt > now_text:
            result["deferred"] += 1
            continue
        if result["attempted"] >= max_assets or time.monotonic() - started >= time_budget:
            result["budget_exhausted"] = True
            break
        result["eligible"] += 1
        result["attempted"] += 1
        try:
            new_id = download_asset(
                conn, url, assets_dir,
                kind=row["kind"],
                caption=row["caption"],
                alt_text=row["alt_text"],
                timeout=timeout,
                max_bytes=max_bytes,
                max_retries=max_retries,
            )
            if new_id:
                conn.execute("UPDATE assets SET storage_status='local' WHERE id=?", (asset_id,))
                conn.execute("DELETE FROM asset_recovery_state WHERE asset_id=?", (asset_id,))
                result["recovered"] += 1
        except Exception as exc:
            log.warning("Recovery failed for asset %d (%s): %s", asset_id, url, exc)
            result["failed"].append({"id": asset_id, "url": url, "error": str(exc)[:200]})
            _, terminal = _record_recovery_failure(
                conn,
                asset_id,
                exc,
                max_attempts=max_attempts,
                backoff_hours=backoff_hours,
            )
            if terminal:
                result["terminal"] += 1
            result["marked_missing"] += 1
    conn.commit()
    return result
