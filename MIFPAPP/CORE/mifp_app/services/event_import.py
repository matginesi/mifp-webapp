"""Validation and atomic installation of one public event website.

The website archive and the existing ``mifp-content`` metadata archive remain
separate contracts.  This module coordinates them; it deliberately delegates
INFO parsing/importing to :mod:`data_portability` instead of inventing a second
metadata format.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import urlsplit
from uuid import uuid4

import yaml

from ..config import Config
from .assets import AssetWriteSession
from .data_portability import import_zip_payload, parse_zip_payload
from .portability_contract import CONTENT_FORMAT, CONTENT_FORMAT_VERSION
from .registration_safety import is_registration_path, is_safe_public_registration_scaffold

WEBSITE_REQUIRED = {"conference.yaml"}
WEBSITE_INDEXES = {"index.html", "index.htm", "index.php"}
PHP_SUFFIXES = {".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8"}
SENSITIVE_DIRS = {".git", ".svn"}
SENSITIVE_NAMES = {
    ".env", ".htpasswd", ".netrc", ".npmrc", ".pgpass", ".s3cfg",
    "credentials.json", "credential.json", "creds.json", "secrets.json",
    "secret.json", "tokens.json", "token.json", "shadow",
}
SENSITIVE_SUFFIXES = {
    ".bak", ".cer", ".crt", ".db", ".key", ".old", ".orig", ".p12",
    ".pem", ".pfx", ".sql", ".sqlite", ".sqlite3",
}
SAFE_PATH_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,99}")
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,99}")
UID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass
class Check:
    level: str
    message: str


@dataclass
class PackageInfo:
    kind: str
    filename: str
    zip_bytes: int
    sha256: str
    files: int
    unpacked_bytes: int
    event: str = ""
    version: str = ""
    root: str = ""
    uid: str = ""
    slug: str = ""
    title: str = ""
    start_date: str = ""
    end_date: str = ""
    url: str = ""
    primary_link: str = ""
    php_files: int = 0
    assets: int = 0
    manifest_status: str = "not applicable"
    scope: str = ""
    record: dict[str, Any] = field(default_factory=dict, repr=False)

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("record", None)
        return value


@dataclass
class Inspection:
    website: PackageInfo | None
    info: PackageInfo | None
    checks: list[Check]
    destination: str
    events_host: str
    db_action: str

    @property
    def errors(self) -> list[Check]:
        return [item for item in self.checks if item.level == "ERROR"]

    @property
    def warnings(self) -> list[Check]:
        return [item for item in self.checks if item.level == "WARNING"]

    def public_dict(self) -> dict[str, Any]:
        return {
            "website": self.website.public_dict() if self.website else None,
            "info": self.info.public_dict() if self.info else None,
            "checks": [asdict(item) for item in self.checks],
            "destination": self.destination,
            "events_host": self.events_host,
            "db_action": self.db_action,
        }


def _human_event(data: dict[str, Any], fallback: str = "") -> str:
    return str(data.get("acronym") or data.get("short_name") or data.get("title") or fallback).strip()


def normalize_destination(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    if not raw or raw.startswith(".") or WINDOWS_DRIVE_RE.match(raw):
        raise ValueError("Destination path must be a non-empty relative event path.")
    parts = raw.split("/")
    if len(parts) > 4 or any(not SAFE_PATH_PART.fullmatch(part) or part in {".", ".."} for part in parts):
        raise ValueError("Destination path contains unsupported characters or segments.")
    return "/".join(parts)


def events_origin(domain: str) -> str:
    host = str(domain or "").strip().lower().rstrip("/")
    if "://" in host:
        parsed = urlsplit(host)
        host = parsed.netloc
    if not host or "/" in host or "@" in host:
        raise ValueError("EVENTS_DOMAIN is invalid.")
    scheme = "http" if host in {"localhost", "127.0.0.1"} or host.endswith(".localhost") else "https"
    return f"{scheme}://{host}"


def destination_url(domain: str, destination: str) -> str:
    return f"{events_origin(domain)}/{normalize_destination(destination)}/"


def php_execution_status(destination: str, state_path: Path | None) -> str:
    """Read the host-owned PHP allow-list without ever modifying it."""
    if state_path is None:
        return "unknown"
    path = Path(state_path)
    if not path.exists():
        return "unknown"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("PHP allow-list state is unsafe.")
    selected = normalize_destination(destination)
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value:
            continue
        enabled = normalize_destination(value)
        if enabled == selected or enabled.startswith(selected + "/"):
            return "enabled"
    return "disabled"


def _normalized_member(name: str) -> str:
    value = name.replace("\\", "/")
    if not value or "\x00" in value or value.startswith("/") or WINDOWS_DRIVE_RE.match(value):
        raise ValueError(f"Unsafe ZIP member path: {name!r}")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe ZIP member path: {name!r}")
    return path.as_posix().rstrip("/")


def _sensitive_reason(relative: PurePosixPath) -> str | None:
    parts = [part.casefold() for part in relative.parts]
    if any(part in SENSITIVE_DIRS for part in parts):
        return "repository metadata"
    name = relative.name.casefold()
    if name in SENSITIVE_NAMES or name.startswith(".env."):
        return "secret/config file"
    if name.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")):
        return "SSH key material"
    if name.endswith((".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm")):
        return "database payload"
    if relative.suffix.casefold() in SENSITIVE_SUFFIXES:
        return "database/backup/key payload"
    if "regform" in parts and "registrations" in parts:
        return "private registration data"
    return None


def _zip_entries(zf: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, str]], int]:
    infos = zf.infolist()
    if len(infos) > Config.EVENT_IMPORT_MAX_FILES:
        raise ValueError(f"ZIP exceeds the {Config.EVENT_IMPORT_MAX_FILES} member limit.")
    entries: list[tuple[zipfile.ZipInfo, str]] = []
    seen: set[str] = set()
    unpacked = 0
    for info in infos:
        normalized = _normalized_member(info.filename)
        key = normalized.casefold()
        if key in seen:
            raise ValueError(f"ZIP has a duplicate normalized path: {normalized}")
        seen.add(key)
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        if kind and kind not in {stat.S_IFREG, stat.S_IFDIR}:
            raise ValueError(f"ZIP member is a link or special file: {normalized}")
        if info.is_dir():
            continue
        unpacked += info.file_size
        if unpacked > Config.EVENT_IMPORT_MAX_UNPACKED_BYTES:
            raise ValueError("ZIP expands beyond the configured event import limit.")
        if info.file_size and info.compress_size == 0:
            raise ValueError(f"ZIP member has an invalid compression ratio: {normalized}")
        if info.compress_size and info.file_size / info.compress_size > Config.EVENT_IMPORT_MAX_COMPRESSION_RATIO:
            raise ValueError(f"ZIP member has an unsafe compression ratio: {normalized}")
        entries.append((info, normalized))
    if not entries:
        raise ValueError("ZIP contains no files.")
    return entries, unpacked


def _load_zip(path: Path) -> tuple[zipfile.ZipFile, list[tuple[zipfile.ZipInfo, str]], int]:
    try:
        zf = zipfile.ZipFile(path)
        entries, unpacked = _zip_entries(zf)
        bad = zf.testzip()
        if bad:
            raise ValueError(f"ZIP integrity check failed at {bad}.")
        return zf, entries, unpacked
    except zipfile.BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid ZIP archive.") from exc


def detect_package(path: Path) -> str:
    zf, entries, _ = _load_zip(path)
    with zf:
        names = {name.casefold() for _, name in entries}
        if {"manifest.json", "records.jsonl"} <= names:
            return "info"
        yaml_paths = [name for _, name in entries if PurePosixPath(name).name.casefold() == "conference.yaml"]
        if len(yaml_paths) == 1:
            return "website"
    raise ValueError("Package type could not be detected from its contents.")


def inspect_website(path: Path, filename: str) -> PackageInfo:
    zf, entries, unpacked = _load_zip(path)
    with zf:
        files = [name for _, name in entries]
        yaml_paths = [name for name in files if PurePosixPath(name).name.casefold() == "conference.yaml"]
        if len(yaml_paths) != 1:
            raise ValueError("WEBSITE must contain exactly one conference.yaml.")
        yaml_path = PurePosixPath(yaml_paths[0])
        if len(yaml_path.parts) != 2:
            raise ValueError("WEBSITE must contain one safe top-level conference directory.")
        root = yaml_path.parts[0]
        if not SAFE_PATH_PART.fullmatch(root):
            raise ValueError("WEBSITE top-level directory is not a safe event path.")
        prefix = root + "/"
        if any(not name.startswith(prefix) for name in files):
            raise ValueError("WEBSITE must have exactly one top-level conference directory.")
        relative_names = {name[len(prefix):] for name in files}
        if not WEBSITE_REQUIRED <= {name.casefold() for name in relative_names}:
            raise ValueError("WEBSITE is missing conference.yaml.")
        if not WEBSITE_INDEXES & {name.casefold() for name in relative_names}:
            raise ValueError("WEBSITE root is missing index.html, index.htm, or index.php.")
        entry_by_name = {name: info for info, name in entries}
        for name in files:
            relative = PurePosixPath(name[len(prefix):])
            if is_registration_path(relative):
                info = entry_by_name[name]
                payload = zf.read(info)
                if is_safe_public_registration_scaffold(relative, payload):
                    continue
                raise ValueError(
                    f"WEBSITE contains prohibited private registration data: {relative.as_posix()}"
                )
            reason = _sensitive_reason(relative)
            if reason:
                raise ValueError(f"WEBSITE contains prohibited {reason}: {relative.as_posix()}")
        yaml_info = zf.getinfo(yaml_paths[0])
        if yaml_info.file_size > 2 * 1024 * 1024:
            raise ValueError("conference.yaml exceeds 2 MB.")
        try:
            config = yaml.safe_load(zf.read(yaml_info).decode("utf-8-sig"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ValueError("conference.yaml is not valid UTF-8 YAML.") from exc
        if not isinstance(config, dict):
            raise ValueError("conference.yaml must contain a mapping.")
        conference = config.get("conference") if isinstance(config.get("conference"), dict) else {}
        site = config.get("site") if isinstance(config.get("site"), dict) else {}
        template = config.get("template") if isinstance(config.get("template"), dict) else {}
        version = str(template.get("version") or "").strip()
        version_paths = [name for name in files if PurePosixPath(name).name.casefold() == "conference.version.json"]
        if len(version_paths) > 1:
            raise ValueError("WEBSITE contains multiple conference.version.json files.")
        if version_paths:
            try:
                version_doc = json.loads(zf.read(version_paths[0]).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("conference.version.json is invalid.") from exc
            if not isinstance(version_doc, dict):
                raise ValueError("conference.version.json must contain an object.")
            version = str(version_doc.get("version") or version).strip()
        php_files = sum(PurePosixPath(name).suffix.casefold() in PHP_SUFFIXES for name in files)
        return PackageInfo(
            kind="website", filename=filename, zip_bytes=path.stat().st_size,
            sha256=_sha256(path), files=len(files), unpacked_bytes=unpacked,
            event=_human_event(conference, root), version=version, root=root,
            slug=str(site.get("slug") or "").strip(),
            title=str(conference.get("full_name") or site.get("title") or "").strip(),
            start_date=str(conference.get("start_date") or "").strip(),
            end_date=str(conference.get("end_date") or "").strip(),
            url=str(site.get("base_url") or site.get("canonical_url") or "").strip(),
            php_files=php_files,
        )


def inspect_info(path: Path, filename: str) -> PackageInfo:
    package = parse_zip_payload(path)
    manifest = package["manifest"]
    if manifest.get("format") != CONTENT_FORMAT or manifest.get("format_version") != CONTENT_FORMAT_VERSION:
        raise ValueError(f"INFO must use {CONTENT_FORMAT} version {CONTENT_FORMAT_VERSION}.")
    manifest_scope = str(manifest.get("scope") or "").strip()
    if manifest_scope not in {"events", "all"}:
        raise ValueError("INFO manifest scope must be events or all.")
    if package.get("missing_assets"):
        raise ValueError("INFO is missing declared asset files.")
    records = [json.loads(line) for line in package["records_jsonl"].splitlines() if line.strip()]
    if len(records) != 1 or records[0].get("type") != "event":
        raise ValueError("INFO must contain exactly one event record.")
    record = records[0]
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    uid = str(data.get("uid") or "").strip()
    slug = str(data.get("slug") or "").strip()
    if not UID_RE.fullmatch(uid):
        raise ValueError("INFO event UID is missing or invalid.")
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("INFO event slug is missing or invalid.")
    links = record.get("links") if isinstance(record.get("links"), list) else []
    primary = next((str(link.get("url") or "") for link in links if isinstance(link, dict) and link.get("is_primary")), "")
    return PackageInfo(
        kind="info", filename=filename, zip_bytes=path.stat().st_size,
        sha256=_sha256(path), files=2 + len(package["asset_files"]),
        unpacked_bytes=0, event=_human_event(data, slug),
        version=str(manifest.get("format_version")), uid=uid, slug=slug,
        title=str(data.get("title") or "").strip(),
        start_date=str(data.get("start_date") or "").strip(),
        end_date=str(data.get("end_date") or "").strip(),
        url=str(data.get("remote_url") or "").strip(), primary_link=primary,
        assets=len(package["asset_files"]), manifest_status="SHA-256 verified",
        scope=manifest_scope, record=record,
    )


def _identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def inspect_packages(conn, website_path: Path | None, info_path: Path | None,
                     website_name: str = "", info_name: str = "", *,
                     assets_dir: Path | None = None, events_domain: str | None = None) -> Inspection:
    checks: list[Check] = []
    website = inspect_website(website_path, website_name) if website_path else None
    info = inspect_info(info_path, info_name) if info_path else None
    if not website and not info:
        raise ValueError("Choose at least one WEBSITE or INFO package.")
    checks.append(Check("PASS", f"{('WEBSITE + INFO' if website and info else (website or info).kind.upper())} package contract validated."))
    if website:
        checks.append(Check("PASS", "WEBSITE paths, object types, limits, sensitive-file rules, YAML, and ZIP integrity passed."))
        if website.php_files:
            checks.append(Check("WARNING", f"PHP files detected: {website.php_files}. Execution remains disabled after import."))
    if info:
        # Exercise the canonical record validator without writing to the DB.
        summary = import_zip_payload(
            conn, info_path, info.scope or "events", assets_dir or Config.ASSETS_DIR,
            dry_run=True, commit=False,
        )
        if summary.get("errors") or summary.get("asset_errors"):
            errors = summary.get("errors") or summary.get("asset_errors")
            raise ValueError(f"INFO record validation failed: {errors[0].get('error', 'invalid record')}")
        checks.append(Check("PASS", "INFO manifest, records hash, JSONL record, and declared assets passed."))
    if website and info:
        if _identity(website.root) != _identity(info.slug):
            checks.append(Check("ERROR", "WEBSITE root and INFO slug identify different events."))
        else:
            checks.append(Check("PASS", "WEBSITE root and INFO slug match case-insensitively."))
        web_acronym = re.sub(r"\d+$", "", _identity(website.event))
        info_identities = tuple(
            value for value in (_identity(info.slug), _identity(info.title), _identity(info.event)) if value
        )
        if web_acronym and info_identities and not any(
            value.startswith(web_acronym) for value in info_identities
        ):
            checks.append(Check("ERROR", "WEBSITE and INFO event identities do not match."))
        elif web_acronym:
            checks.append(Check("PASS", "WEBSITE acronym is consistent with INFO identity."))
        for label, left, right in (("start date", website.start_date, info.start_date), ("end date", website.end_date, info.end_date)):
            if left and right and left != right:
                checks.append(Check("ERROR", f"WEBSITE and INFO {label} differ ({left} vs {right})."))
        if website.title and info.title and _identity(website.title) != _identity(info.title):
            checks.append(Check("WARNING", "WEBSITE and INFO titles differ; review the canonical metadata title."))
        for label, url in (("WEBSITE base URL", website.url), ("INFO remote_url", info.url), ("INFO primary link", info.primary_link)):
            if url:
                try:
                    parsed = urlsplit(url)
                    host = parsed.netloc
                except ValueError:
                    parsed = None
                    host = ""
                if parsed is None or parsed.scheme not in {"http", "https"} or not host:
                    checks.append(Check("ERROR", f"{label} is not a valid public HTTP(S) URL."))
                elif parsed.path.strip("/") and _identity(parsed.path.strip("/").split("/")[0]) != _identity(website.root):
                    checks.append(Check("WARNING", f"{label} uses a path different from the WEBSITE root."))
    destination = website.root if website else (info.slug if info else "")
    destination = normalize_destination(destination)
    existing = None
    if info:
        existing = conn.execute("SELECT id FROM events WHERE uid=? OR slug=? ORDER BY id LIMIT 1", (info.uid, info.slug)).fetchone()
    db_action = "UPDATE" if existing else ("CREATE" if info else "SKIP")
    return Inspection(
        website, info, checks, destination,
        events_origin(events_domain or Config.EVENTS_DOMAIN), db_action,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_upload(stream: BinaryIO, target: Path, limit: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with target.open("xb") as output:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError(f"ZIP exceeds the {limit} byte upload limit.")
            output.write(chunk)
    if total == 0:
        target.unlink(missing_ok=True)
        raise ValueError("Uploaded ZIP is empty.")


def staging_root(tmp_dir: Path) -> Path:
    return Path(tmp_dir) / "event-imports"


def cleanup_staging(tmp_dir: Path, ttl: int) -> None:
    root = staging_root(tmp_dir)
    if not root.is_dir():
        return
    cutoff = time.time() - ttl
    for child in root.iterdir():
        try:
            if child.is_dir() and not child.is_symlink() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
        except OSError:
            continue


def create_staging(tmp_dir: Path) -> tuple[str, Path]:
    token = uuid4().hex
    path = staging_root(tmp_dir) / token
    path.mkdir(parents=True, mode=0o700)
    return token, path


def resolve_staging(tmp_dir: Path, token: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ValueError("Import staging token is invalid.")
    root = staging_root(tmp_dir).resolve()
    path = (root / token).resolve()
    if path.parent != root or not path.is_dir() or path.is_symlink():
        raise ValueError("Import staging has expired; validate the packages again.")
    return path


def extract_website(path: Path, target: Path, expected_root: str) -> None:
    zf, entries, _ = _load_zip(path)
    prefix = expected_root + "/"
    target.mkdir(parents=True, mode=0o755, exist_ok=True)
    # EVENTS_ROOT itself is group-gated by the host. Public conference files
    # remain world-readable below that gate so Caddy does not need a host group
    # identity inside the application container.
    os.chmod(target, 0o755)
    with zf:
        for info, name in entries:
            if not name.startswith(prefix):
                raise ValueError("WEBSITE root changed during import validation.")
            relative = name[len(prefix):]
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(destination, 0o644)
    for directory in sorted((item for item in target.rglob("*") if item.is_dir()), reverse=True):
        os.chmod(directory, 0o755)


def apply_import(conn, inspection: Inspection, website_path: Path | None, info_path: Path | None,
                 *, events_root: Path, assets_dir: Path, destination: str,
                 publish_website: bool, import_metadata: bool,
                 forthcoming: bool | None = None,
                 replace: bool, keep_rollback: bool, events_domain: str | None = None,
                 php_state_path: Path | None = None, require_php_state: bool = False) -> dict[str, Any]:
    destination = normalize_destination(destination)
    configured_root = Path(events_root)
    if configured_root.is_symlink():
        raise ValueError("Configured EVENTS_ROOT must not be a symlink.")
    root = configured_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = (root / destination).resolve()
    try:
        final.relative_to(root)
    except ValueError as exc:
        raise ValueError("Destination escapes EVENTS_ROOT.") from exc
    if publish_website and not website_path:
        raise ValueError("Publish website requires a validated WEBSITE package.")
    if import_metadata and not info_path:
        raise ValueError("Metadata import requires a validated INFO package.")
    if final.exists() and (final.is_symlink() or not final.is_dir()):
        raise ValueError("Existing destination is not a safe directory.")
    if publish_website and final.exists() and not replace:
        raise ValueError("Destination already exists; select atomic replace/update.")
    if publish_website:
        php_status = php_execution_status(destination, php_state_path)
        if require_php_state and php_status == "unknown":
            raise ValueError("PHP allow-list state is unavailable; event publication fails closed.")
        if php_status == "enabled":
            raise ValueError(
                f"PHP is enabled below this destination. Run 'sudo mifpctl events-php-disable "
                f"{destination}' (or its enabled subpath) before replacing website code."
            )

    stage: Path | None = None
    backup: Path | None = None
    prior_backup: Path | None = None
    installed = False
    moved_old = False
    asset_session = AssetWriteSession(Path(assets_dir))
    summary: dict[str, Any] = {"website": "skipped", "metadata": "skipped", "destination": destination}
    try:
        if publish_website:
            stage = Path(tempfile.mkdtemp(prefix=f".{final.name}.stage-", dir=final.parent))
            extract_website(website_path, stage, inspection.website.root)
        conn.execute("BEGIN IMMEDIATE")
        event_id = None
        metadata_summary = None
        if import_metadata:
            metadata_summary = import_zip_payload(
                conn, info_path, inspection.info.scope or "events", Path(assets_dir), dry_run=False,
                commit=False, file_session=asset_session,
                source_name=inspection.info.filename,
            )
            if metadata_summary.get("errors") or metadata_summary.get("asset_errors"):
                raise ValueError("INFO import reported record or asset errors.")
            row = conn.execute(
                "SELECT id FROM events WHERE uid=? OR slug=? ORDER BY id LIMIT 1",
                (inspection.info.uid, inspection.info.slug),
            ).fetchone()
            if not row:
                raise ValueError("Imported event record could not be resolved.")
            event_id = int(row["id"])
            if forthcoming is not None:
                # Forthcoming visibility is an editorial operator choice, not a
                # transport property of the INFO package. Public visibility also
                # depends on the Event review status (normally ``published``).
                conn.execute(
                    "UPDATE events SET is_featured=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (1 if forthcoming else 0, event_id),
                )

        if publish_website:
            backup = final.parent / f".{final.name}.rollback"
            if final.exists():
                if backup.exists():
                    if backup.is_symlink() or not backup.is_dir():
                        raise ValueError("Rollback location is unsafe.")
                    prior_backup = final.parent / f".{final.name}.rollback.previous-{uuid4().hex}"
                    backup.rename(prior_backup)
                final.rename(backup)
                moved_old = True
            stage.rename(final)
            installed = True

        if inspection.info:
            existing_event = conn.execute(
                "SELECT id FROM events WHERE uid=? OR slug=? ORDER BY id LIMIT 1",
                (inspection.info.uid, inspection.info.slug),
            ).fetchone()
            event_id = event_id or (int(existing_event["id"]) if existing_event else None)
        domain = events_domain or Config.EVENTS_DOMAIN
        if publish_website or inspection.website:
            manifest = {
                "event_import": 1,
                "website_sha256": inspection.website.sha256 if inspection.website else None,
                "website_files": inspection.website.files if inspection.website else 0,
                "website_bytes": inspection.website.unpacked_bytes if inspection.website else 0,
                "php_files": inspection.website.php_files if inspection.website else 0,
                "php_execution": "disabled",
                "validation": "passed",
            }
            slug = inspection.info.slug if inspection.info else destination.casefold()
            title = inspection.info.title if inspection.info else inspection.website.title or inspection.website.event
            existing_site = conn.execute(
                "SELECT id FROM conference_sites WHERE event_id=? OR slug=? ORDER BY event_id IS NULL, id LIMIT 1",
                (event_id, slug),
            ).fetchone()
            values = (
                title, inspection.website.event if inspection.website else inspection.info.event,
                inspection.info.start_date if inspection.info else inspection.website.start_date,
                inspection.info.end_date if inspection.info else inspection.website.end_date,
                destination_url(domain, destination), f"/{destination}/", event_id, destination,
                inspection.website.version if inspection.website else inspection.info.version,
                inspection.website.sha256 if inspection.website else inspection.info.sha256,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                "published" if publish_website else "staged",
            )
            if existing_site:
                conn.execute(
                    """UPDATE conference_sites SET title=?,acronym=?,start_date=?,end_date=?,
                           canonical_url=?,deploy_base_path=?,event_id=?,public_path=?,
                           source_format='legacy-static',source_version=?,package_sha256=?,
                           package_manifest_json=?,deploy_status=?,imported_at=CURRENT_TIMESTAMP,
                           published_at=CASE WHEN ?='published' THEN CURRENT_TIMESTAMP ELSE published_at END,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (*values, values[-1], int(existing_site["id"])),
                )
            else:
                conn.execute(
                    """INSERT INTO conference_sites(
                       slug,title,acronym,start_date,end_date,canonical_url,deploy_base_path,
                       event_id,public_path,source_format,source_version,package_sha256,
                       package_manifest_json,deploy_status,imported_at,published_at)
                       VALUES(?,?,?,?,?,?,?,?,?,'legacy-static',?,?,?,?,CURRENT_TIMESTAMP,
                              CASE WHEN ?='published' THEN CURRENT_TIMESTAMP ELSE NULL END)""",
                    (slug, *values, values[-1]),
                )
        conn.commit()
        summary["metadata"] = inspection.db_action if import_metadata else "skipped"
        summary["website"] = "published" if publish_website else "skipped"
        summary["forthcoming"] = forthcoming if import_metadata else None
        if publish_website:
            summary["url"] = destination_url(domain, destination)
        if backup and backup.exists() and not keep_rollback:
            shutil.rmtree(backup)
        if prior_backup and prior_backup.exists():
            shutil.rmtree(prior_backup)
        return summary
    except Exception:
        conn.rollback()
        asset_session.rollback()
        if installed and final.exists():
            shutil.rmtree(final)
        if moved_old and backup and backup.exists():
            backup.rename(final)
        if prior_backup and prior_backup.exists() and backup and not backup.exists():
            prior_backup.rename(backup)
        raise
    finally:
        if stage and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
