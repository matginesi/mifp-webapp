from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from mifp_app.services.conference_packages import (
    inspect_editor_package,
    load_stored_package,
    normalize_public_path,
    store_editor_package,
)

PEOPLE_HEADER = (
    "First Name,Last Name,Category,Role,Affiliation,Country,Presentation Title,"
    "Presentation Type,Image,Visible\n"
)
PROGRAM_HEADER = (
    "Day,Date,Start Time,End Time,Type,Title,Speaker,Affiliation,Chair,Location,Notes,Visible\n"
)


def _editor_zip(*, prefix: str = "", extra: dict[str, bytes | str] | None = None) -> bytes:
    config = {
        "template": {"name": "MIFP Static Conference Template", "version": 1.5, "schema_version": "1"},
        "site": {
            "title": "PLMCN 2027",
            "short_name": "PLMCN-2027",
            "year": 2027,
            "base_url": "https://events.mifp.eu/PLMCN-2027/",
        },
        "conference": {
            "full_name": "Physics of Low-dimensional and Molecular Conductors 2027",
            "acronym": "PLMCN",
            "start_date": "2027-09-20",
            "end_date": "2027-09-24",
            "city": "Rome",
            "country": "Italy",
            "venue": "Tor Vergata",
            "email": "conference@mifp.eu",
        },
    }
    files: dict[str, bytes | str] = {
        "conference.yaml": yaml.safe_dump(config, sort_keys=False),
        "conference.version.json": json.dumps(
            {"schema": 1, "version": "0.4.2", "status": "ready", "updated_at": "", "history": []}
        ),
        "data/people.csv": PEOPLE_HEADER + "Ada,Lovelace,Speaker,Speaker,MIFP,Italy,Computing,Talk,,true\n",
        "data/program.csv": PROGRAM_HEADER + "1,2027-09-20,09:00,10:00,talk,Opening,Ada Lovelace,MIFP,,Main hall,,true\n",
        "index.html": "<!doctype html><title>PLMCN</title>",
        "assets/site.css": "body{}",
        "regform/index.php": "<?php echo 'ok';",
        "regform/settings.yaml": "regform:\n  enabled: true\n  backend:\n    storage_path: registrations\n",
    }
    files.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(prefix + name, content)
    return buffer.getvalue()


def test_editor_package_is_validated_and_metadata_is_normalized(tmp_path: Path) -> None:
    payload = _editor_zip()
    package = inspect_editor_package(payload)

    assert package.package_format == "conference-editor"
    assert package.schema_version == 1
    assert package.source_version == "0.4.2"
    assert package.source_status == "ready"
    assert package.title.startswith("Physics of Low-dimensional")
    assert package.acronym == "PLMCN"
    assert package.year == 2027
    assert package.start_date == "2027-09-20"
    assert package.city == "Rome"
    assert package.canonical_url == "https://events.mifp.eu/PLMCN-2027/"
    assert package.people_rows == 1
    assert package.program_rows == 1
    assert package.has_registration is True
    assert package.sha256 == hashlib.sha256(payload).hexdigest()

    stored = store_editor_package(payload, tmp_path, "plmcn-2027")
    assert stored.package_path == tmp_path / "plmcn-2027" / "packages" / f"{package.sha256}.zip"
    assert stored.source_path == tmp_path / "plmcn-2027" / "sources" / package.sha256
    assert (stored.source_path / "conference.yaml").is_file()
    assert (stored.source_path / "regform" / "index.php").is_file()
    assert load_stored_package(tmp_path, "plmcn-2027", package.sha256) == payload

    # Exact re-import is idempotent and never creates a mutable 'current' tree.
    second = store_editor_package(payload, tmp_path, "plmcn-2027")
    assert second.package_path == stored.package_path
    assert sorted(path.name for path in (tmp_path / "plmcn-2027" / "sources").iterdir()) == [package.sha256]


def test_editor_package_accepts_one_common_wrapper_directory() -> None:
    package = inspect_editor_package(_editor_zip(prefix="PLMCN-2027/"))
    assert package.root_prefix == "PLMCN-2027"
    assert package.title.startswith("Physics of Low-dimensional")


def test_editor_package_rejects_unsafe_or_executable_content() -> None:
    with pytest.raises(ValueError, match="Unsafe file"):
        inspect_editor_package(_editor_zip(extra={"../escape.txt": "bad"}))

    with pytest.raises(ValueError, match="PHP files are only permitted"):
        inspect_editor_package(_editor_zip(extra={"shell.php": "<?php"}))

    with pytest.raises(ValueError, match="private regform/registrations"):
        inspect_editor_package(
            _editor_zip(extra={"regform/registrations/private.csv": "name,email\nA,a@example.test\n"})
        )



def test_editor_package_allows_public_registration_guard_scaffold() -> None:
    package = inspect_editor_package(_editor_zip(extra={
        "regform/registrations/.gitignore": "registrations.csv\n.secret.php\n",
        "regform/registrations/.htaccess": (
            "# deny direct access\n<IfModule mod_rewrite.c>\nRewriteEngine On\n"
            "RewriteRule ^ - [F,L]\n</IfModule>\n"
        ),
        "regform/registrations/index.php": "<?php\nhttp_response_code(404);\nexit;\n",
    }))
    assert package.has_registration is True

def test_public_path_preserves_historic_case_and_rejects_traversal() -> None:
    assert normalize_public_path("/PLMCN-2025/") == "PLMCN-2025"
    assert normalize_public_path("Archive/ICP2DC-2024") == "Archive/ICP2DC-2024"
    with pytest.raises(ValueError):
        normalize_public_path("../PLMCN-2025")
    with pytest.raises(ValueError):
        normalize_public_path("PLMCN 2025")
