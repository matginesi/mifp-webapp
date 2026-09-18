from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "deploy" / "check-events-archive.py"


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(root)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_events_archive_preflight_accepts_static_archive_and_reports_legacy_links(tmp_path: Path) -> None:
    archive = tmp_path / "events"
    archive.mkdir()
    (archive / "index.html").write_text(
        '<a href="https://old.mifp.eu/PLMCN-2018">legacy</a>',
        encoding="utf-8",
    )
    conference = archive / "PLMCN-2018"
    conference.mkdir()
    (conference / "index.html").write_text("historic", encoding="utf-8")
    (conference / "legacy.php").write_text("<?php echo 'historic';", encoding="utf-8")

    result = run_check(archive)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Events archive preflight: OK" in result.stdout
    assert "PHP files:   1 (deny-by-default after import)" in result.stdout
    assert "references to old.mifp.eu" in result.stdout


def test_events_archive_preflight_rejects_private_database_and_env(tmp_path: Path) -> None:
    archive = tmp_path / "events"
    archive.mkdir()
    (archive / "index.html").write_text("historic", encoding="utf-8")
    (archive / ".env").write_text("SECRET=x", encoding="utf-8")
    (archive / "registrations.db").write_bytes(b"sqlite")

    result = run_check(archive)

    assert result.returncode != 0
    assert ".env" in result.stderr
    assert "registrations.db" in result.stderr


def test_events_archive_preflight_rejects_symlink_and_fifo(tmp_path: Path) -> None:
    archive = tmp_path / "events"
    archive.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (archive / "link").symlink_to(outside)
    os.mkfifo(archive / "pipe")

    result = run_check(archive)

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert "file speciale" in result.stderr


def test_preflight_rejects_symlink_document_root(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    (target / "index.html").write_text("ok", encoding="utf-8")
    link = tmp_path / "events-link"
    link.symlink_to(target, target_is_directory=True)

    result = run_check(link)

    assert result.returncode == 2
    assert "symlink" in result.stderr.lower()
