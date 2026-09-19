"""Validation and storage for packages exported by ``mifp-conference-editor``.

The conference editor is the authoring tool.  The MIFP web application does
not reinterpret or rebuild those sites: it validates the exported package,
records its metadata and keeps an immutable source snapshot for a later
publish step.

Nothing extracted by this module is served directly.  Publication is a
separate operation so an uploaded package cannot become executable merely by
being imported into the dashboard.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import yaml

from ..utils.file_safety import open_write_no_follow
from .registration_safety import is_registration_path, is_safe_public_registration_scaffold

MAX_EDITOR_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_EDITOR_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_EDITOR_PACKAGE_FILES = 2_000
MAX_EDITOR_FILE_BYTES = 128 * 1024 * 1024
MAX_EDITOR_CONFIG_BYTES = 2 * 1024 * 1024
MAX_EDITOR_COMPRESSION_RATIO = 200
SUPPORTED_EDITOR_SCHEMA = 1

_REQUIRED_ROOT_FILES = frozenset(
    {
        "conference.yaml",
        "conference.version.json",
        "data/people.csv",
        "data/program.csv",
        "index.html",
    }
)
_PEOPLE_HEADERS = (
    "First Name",
    "Last Name",
    "Category",
    "Role",
    "Affiliation",
    "Country",
    "Presentation Title",
    "Presentation Type",
    "Image",
    "Visible",
)
_PROGRAM_HEADERS = (
    "Day",
    "Date",
    "Start Time",
    "End Time",
    "Type",
    "Title",
    "Speaker",
    "Affiliation",
    "Chair",
    "Location",
    "Notes",
    "Visible",
)
_BLOCKED_EXECUTABLE_SUFFIXES = frozenset(
    {".cgi", ".pl", ".py", ".pyc", ".pyo", ".sh", ".bash", ".phar", ".phtml"}
)
_TBC_VALUES = frozenset({"", "tbc", "new-conference", "n/a", "na", "none", "null"})


@dataclass(frozen=True)
class ConferencePackage:
    """Normalized metadata from one conference-editor package."""

    package_format: str
    schema_version: int
    source_version: str
    source_status: str
    sha256: str
    file_count: int
    expanded_bytes: int
    root_prefix: str
    has_registration: bool
    people_rows: int
    program_rows: int
    title: str
    acronym: str
    year: int | None
    start_date: str
    end_date: str
    venue: str
    city: str
    country: str
    contact_email: str
    canonical_url: str

    def manifest(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StoredConferencePackage:
    """Result of storing a validated immutable source snapshot."""

    package: ConferencePackage
    package_path: Path
    source_path: Path


@dataclass(frozen=True)
class _ArchiveEntry:
    info: zipfile.ZipInfo
    normalized_name: str


def normalize_public_path(value: str) -> str:
    """Validate a case-preserving public path relative to events.mifp.eu.

    ``slug`` remains the lower-case internal storage identifier.  This path is
    deliberately separate because historic conference URLs are case-sensitive.
    """
    value = value.strip().strip("/")
    if not value or len(value) > 160:
        raise ValueError("Public path must contain between 1 and 160 URL-safe characters.")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Public path cannot contain empty, '.' or '..' segments.")
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]*", part) for part in parts):
        raise ValueError(
            "Public path may contain letters, numbers, dots, underscores, tildes and hyphens."
        )
    return value


def _decode_yaml(raw: bytes, filename: str) -> dict[str, Any]:
    if not raw or len(raw) > MAX_EDITOR_CONFIG_BYTES:
        raise ValueError(f"{filename} must be between 1 byte and 2 MB.")
    try:
        parsed = yaml.safe_load(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{filename} is not valid UTF-8 YAML.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{filename} must contain a YAML mapping.")
    return parsed


def _decode_version(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > 256 * 1024:
        raise ValueError("conference.version.json must be between 1 byte and 256 KB.")
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("conference.version.json is not valid UTF-8 JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("conference.version.json must contain a JSON object.")
    return parsed


def _root_prefix(names: set[str]) -> str:
    if _REQUIRED_ROOT_FILES <= names:
        return ""
    first_parts = {PurePosixPath(name).parts[0] for name in names if PurePosixPath(name).parts}
    if len(first_parts) != 1:
        raise ValueError(
            "Conference Editor ZIP must contain conference.yaml at its root "
            "(or inside one common top-level folder)."
        )
    prefix = next(iter(first_parts)) + "/"
    stripped = {name[len(prefix) :] for name in names if name.startswith(prefix)}
    if not _REQUIRED_ROOT_FILES <= stripped:
        missing = sorted(_REQUIRED_ROOT_FILES - stripped)
        raise ValueError("Conference Editor ZIP is missing required files: " + ", ".join(missing))
    return prefix


def _validate_zip_entries(archive: zipfile.ZipFile) -> tuple[list[_ArchiveEntry], str, int]:
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if not infos:
        raise ValueError("Conference Editor ZIP is empty.")
    if len(infos) > MAX_EDITOR_PACKAGE_FILES:
        raise ValueError(f"Conference Editor ZIP contains more than {MAX_EDITOR_PACKAGE_FILES} files.")

    raw_names: set[str] = set()
    expanded_bytes = 0
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        mode = (info.external_attr >> 16) & 0o170000
        if (
            not name
            or len(name) > 1_024
            or "\x00" in name
            or "\\" in name
            or path.is_absolute()
            # PurePosixPath("C:/x").is_absolute() is False; a drive-letter first
            # component must be rejected for parity with the portability loader.
            or ":" in path.parts[0]
            or any(
                part in {"", ".", ".."} or len(part.encode("utf-8")) > 255
                for part in path.parts
            )
            or stat.S_ISLNK(mode)
            or name in raw_names
            or info.flag_bits & 0x1
        ):
            raise ValueError(f"Unsafe file in Conference Editor ZIP: {name!r}")
        if info.file_size > MAX_EDITOR_FILE_BYTES:
            raise ValueError(f"Conference Editor file is too large: {name}")
        if info.compress_size and info.file_size > 1024 * 1024:
            ratio = info.file_size / info.compress_size
            if ratio > MAX_EDITOR_COMPRESSION_RATIO:
                raise ValueError(f"Suspicious compression ratio in Conference Editor ZIP: {name}")
        raw_names.add(name)
        expanded_bytes += info.file_size

    if expanded_bytes > MAX_EDITOR_EXPANDED_BYTES:
        raise ValueError("Conference Editor ZIP expands beyond the 512 MB safety limit.")

    prefix = _root_prefix(raw_names)
    entries: list[_ArchiveEntry] = []
    normalized_names: set[str] = set()
    portable_names: set[str] = set()
    for info in infos:
        normalized = info.filename[len(prefix) :] if prefix else info.filename
        if not normalized:
            continue
        path = PurePosixPath(normalized)
        suffix = path.suffix.lower()
        if normalized in normalized_names:
            raise ValueError(f"Duplicate normalized path in Conference Editor ZIP: {normalized}")
        portable = unicodedata.normalize("NFC", normalized).casefold()
        if portable in portable_names:
            raise ValueError(
                f"Paths collide on a case-insensitive filesystem in Conference Editor ZIP: {normalized}"
            )
        portable_names.add(portable)
        if suffix in _BLOCKED_EXECUTABLE_SUFFIXES:
            raise ValueError(f"Unsupported executable file in Conference Editor ZIP: {normalized}")
        if suffix == ".php" and (not path.parts or path.parts[0] != "regform"):
            raise ValueError(f"PHP files are only permitted below regform/: {normalized}")
        if path.name == ".htaccess" and (not path.parts or path.parts[0] != "regform"):
            raise ValueError(f".htaccess is only permitted below regform/: {normalized}")
        if is_registration_path(path):
            payload = archive.read(info)
            if not is_safe_public_registration_scaffold(path, payload):
                raise ValueError(
                    "Conference packages must not contain private regform/registrations data; "
                    "only the documented public guard scaffolding is permitted."
                )
        normalized_names.add(normalized)
        entries.append(_ArchiveEntry(info=info, normalized_name=normalized))
    return entries, prefix, expanded_bytes


def _csv_rows(raw: bytes, expected_headers: tuple[str, ...], filename: str) -> int:
    if len(raw) > MAX_EDITOR_CONFIG_BYTES:
        raise ValueError(f"{filename} exceeds the 2 MB metadata limit.")
    try:
        stream = io.StringIO(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{filename} is not valid UTF-8 CSV.") from exc
    reader = csv.DictReader(stream)
    headers = tuple(reader.fieldnames or ())
    missing = [header for header in expected_headers if header not in headers]
    if missing:
        raise ValueError(f"{filename} is missing required columns: {', '.join(missing)}")
    count = 0
    for count, _row in enumerate(reader, start=1):
        if count > 20_000:
            raise ValueError(f"{filename} contains too many rows.")
    return count


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in _TBC_VALUES else text[:limit]


def _http_url(value: Any) -> str:
    text = _clean_text(value, limit=2_048)
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        return ""
    return text


def _year(site: dict[str, Any], conference: dict[str, Any]) -> int | None:
    raw = site.get("year")
    try:
        year = int(str(raw).strip())
        if 1900 <= year <= 2200:
            return year
    except (TypeError, ValueError):
        pass
    start = _clean_text(conference.get("start_date"), limit=32)
    match = re.match(r"^(\d{4})-", start)
    if match:
        return int(match.group(1))
    return None


def looks_like_editor_package(raw: bytes) -> bool:
    """Return whether a ZIP advertises the Conference Editor root contract.

    This is intentionally only a classifier. Full safety and schema validation
    still happens in :func:`inspect_editor_package`.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = {info.filename for info in archive.infolist() if not info.is_dir()}
    except zipfile.BadZipFile:
        return False
    if {"conference.yaml", "conference.version.json"} <= names:
        return True
    roots = {PurePosixPath(name).parts[0] for name in names if PurePosixPath(name).parts}
    if len(roots) != 1:
        return False
    prefix = next(iter(roots)) + "/"
    return {prefix + "conference.yaml", prefix + "conference.version.json"} <= names


def inspect_editor_package(raw: bytes) -> ConferencePackage:
    """Validate an editor ZIP and return normalized package metadata."""
    if not raw or len(raw) > MAX_EDITOR_PACKAGE_BYTES:
        raise ValueError("Conference Editor ZIP must be between 1 byte and 512 MB.")
    sha256 = hashlib.sha256(raw).hexdigest()
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("The conference package is not a valid ZIP file.") from exc

    with archive:
        entries, prefix, expanded_bytes = _validate_zip_entries(archive)
        by_name = {entry.normalized_name: entry.info for entry in entries}
        conference_yaml = _decode_yaml(archive.read(by_name["conference.yaml"]), "conference.yaml")
        version = _decode_version(archive.read(by_name["conference.version.json"]))
        people_rows = _csv_rows(
            archive.read(by_name["data/people.csv"]), _PEOPLE_HEADERS, "data/people.csv"
        )
        program_rows = _csv_rows(
            archive.read(by_name["data/program.csv"]), _PROGRAM_HEADERS, "data/program.csv"
        )

        template = conference_yaml.get("template")
        site = conference_yaml.get("site")
        conference = conference_yaml.get("conference")
        if not isinstance(template, dict) or not isinstance(site, dict) or not isinstance(conference, dict):
            raise ValueError(
                "conference.yaml must contain template, site and conference mappings from mifp-conference-editor."
            )
        try:
            template_schema = int(str(template.get("schema_version", "")).strip())
            version_schema = int(version.get("schema"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Conference Editor schema version is missing or invalid.") from exc
        if template_schema != SUPPORTED_EDITOR_SCHEMA or version_schema != SUPPORTED_EDITOR_SCHEMA:
            raise ValueError(
                "Unsupported Conference Editor schema; "
                f"expected {SUPPORTED_EDITOR_SCHEMA}, got template={template_schema}, package={version_schema}."
            )

        source_version = _clean_text(version.get("version"), limit=80)
        if not source_version:
            raise ValueError("conference.version.json must contain a non-empty version.")
        source_status = _clean_text(version.get("status"), limit=40) or "draft"
        names = set(by_name)
        has_registration = "regform/index.php" in names
        if has_registration and "regform/settings.yaml" not in names:
            raise ValueError("Registration-enabled package is missing regform/settings.yaml.")

        return ConferencePackage(
            package_format="conference-editor",
            schema_version=version_schema,
            source_version=source_version,
            source_status=source_status,
            sha256=sha256,
            file_count=len(entries),
            expanded_bytes=expanded_bytes,
            root_prefix=prefix[:-1] if prefix else "",
            has_registration=has_registration,
            people_rows=people_rows,
            program_rows=program_rows,
            title=_clean_text(conference.get("full_name"), limit=300)
            or _clean_text(site.get("title"), limit=300),
            acronym=_clean_text(conference.get("acronym"), limit=80)
            or _clean_text(site.get("short_name"), limit=80),
            year=_year(site, conference),
            start_date=_clean_text(conference.get("start_date"), limit=32),
            end_date=_clean_text(conference.get("end_date"), limit=32),
            venue=_clean_text(conference.get("venue"), limit=300),
            city=_clean_text(conference.get("city"), limit=120),
            country=_clean_text(conference.get("country"), limit=120),
            contact_email=_clean_text(conference.get("email"), limit=254),
            canonical_url=_http_url(site.get("base_url")),
        )


def _extract_editor_package(raw: bytes, destination: Path, package: ConferencePackage) -> None:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries, prefix, _expanded_bytes = _validate_zip_entries(archive)
        if (prefix[:-1] if prefix else "") != package.root_prefix:
            raise ValueError("Conference package root changed during validation.")
        destination.mkdir(parents=True, exist_ok=False)
        for entry in entries:
            target = destination.joinpath(*PurePosixPath(entry.normalized_name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = target.parent.resolve()
            try:
                resolved_parent.relative_to(destination.resolve())
            except ValueError as exc:
                raise ValueError(f"Unsafe extraction path: {entry.normalized_name}") from exc
            with archive.open(entry.info) as source, open_write_no_follow(target) as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(target, 0o640)


def store_editor_package(raw: bytes, conferences_root: Path, slug: str) -> StoredConferencePackage:
    """Store an editor package as immutable ZIP + normalized extracted source.

    Existing snapshots are never overwritten.  Re-importing exactly the same
    bytes is idempotent and simply returns the already stored paths.
    """
    package = inspect_editor_package(raw)
    workspace = (conferences_root / slug).resolve()
    root = conferences_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        workspace.relative_to(root)
    except ValueError as exc:
        raise ValueError("Unsafe conference workspace path.") from exc
    workspace.mkdir(parents=True, exist_ok=True)

    packages_dir = workspace / "packages"
    sources_dir = workspace / "sources"
    packages_dir.mkdir(mode=0o750, exist_ok=True)
    sources_dir.mkdir(mode=0o750, exist_ok=True)
    package_path = packages_dir / f"{package.sha256}.zip"
    source_path = sources_dir / package.sha256

    if not package_path.exists():
        fd, temporary_name = tempfile.mkstemp(prefix=".package-", suffix=".zip", dir=packages_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o640)
            os.replace(temporary_name, package_path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
    elif hashlib.sha256(package_path.read_bytes()).hexdigest() != package.sha256:
        raise RuntimeError("Stored conference package checksum mismatch.")

    if not source_path.exists():
        staging = sources_dir / f".incoming-{uuid4().hex}"
        try:
            _extract_editor_package(raw, staging, package)
            os.replace(staging, source_path)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    return StoredConferencePackage(
        package=package,
        package_path=package_path,
        source_path=source_path,
    )


def load_stored_package(conferences_root: Path, slug: str, sha256: str) -> bytes:
    """Load a previously imported immutable package and verify its checksum."""
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha256 or "")):
        raise ValueError("Stored conference package checksum is invalid.")
    root = conferences_root.resolve()
    package_path = (root / slug / "packages" / f"{sha256}.zip").resolve()
    try:
        package_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Unsafe stored conference package path.") from exc
    if not package_path.is_file() or package_path.is_symlink():
        raise ValueError("Stored Conference Editor package is missing.")
    payload = package_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != sha256:
        raise ValueError("Stored Conference Editor package failed checksum verification.")
    return payload
