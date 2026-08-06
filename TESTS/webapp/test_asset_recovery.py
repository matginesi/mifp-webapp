from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    return conn


def test_download_attempt_limit_is_global_across_url_candidates(monkeypatch, tmp_path):
    from mifp_app.services import assets

    attempts: list[str] = []

    monkeypatch.setattr(
        assets,
        "_download_url_candidates",
        lambda _url: ["https://one.example/a.pdf", "https://two.example/a.pdf", "https://three.example/a.pdf"],
    )
    monkeypatch.setattr(assets, "_validate_and_resolve", lambda url, resolve_dns=False: (url, None))
    monkeypatch.setattr(assets.time, "sleep", lambda _seconds: None)

    def fail(request, timeout):
        attempts.append(request.full_url)
        raise TimeoutError("network timeout")

    monkeypatch.setattr(assets, "urlopen", fail)
    with pytest.raises(TimeoutError):
        assets._download_with_retries(
            "https://source.example/a.pdf",
            timeout=0.01,
            max_bytes=1024,
            max_retries=2,
        )
    assert attempts == ["https://one.example/a.pdf", "https://two.example/a.pdf"]


def test_recovery_queue_persists_cooldown_and_stops_at_limit(monkeypatch, tmp_path):
    from mifp_app.services import assets

    conn = _connection()
    conn.execute(
        """
        INSERT INTO assets(filename,path,kind,source_url,storage_status,is_external)
        VALUES('paper.pdf','pdf/paper.pdf','pdf','https://files.example/paper.pdf','missing',0)
        """
    )
    conn.commit()

    calls = 0

    def fail_download(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("temporary timeout")

    monkeypatch.setattr(assets, "download_asset", fail_download)

    first = assets.recover_missing_assets(
        conn,
        tmp_path,
        max_assets=10,
        max_attempts=2,
        time_budget=10,
        backoff_hours=1,
    )
    assert first["attempted"] == 1
    assert first["terminal"] == 0
    assert calls == 1

    deferred = assets.recover_missing_assets(
        conn,
        tmp_path,
        max_assets=10,
        max_attempts=2,
        time_budget=10,
        backoff_hours=1,
    )
    assert deferred["attempted"] == 0
    assert deferred["deferred"] == 1
    assert calls == 1

    final = assets.recover_missing_assets(
        conn,
        tmp_path,
        max_assets=10,
        max_attempts=2,
        time_budget=10,
        backoff_hours=1,
        force=True,
    )
    state = conn.execute(
        "SELECT attempts, terminal FROM asset_recovery_state WHERE asset_id=1"
    ).fetchone()
    assert final["attempted"] == 1
    assert final["terminal"] == 1
    assert dict(state) == {"attempts": 2, "terminal": 1}
    assert calls == 2


def test_recovery_batch_caps_attempted_assets(monkeypatch, tmp_path):
    from mifp_app.services import assets

    conn = _connection()
    for index in range(4):
        conn.execute(
            """
            INSERT INTO assets(filename,path,kind,source_url,storage_status,is_external)
            VALUES(?,?,?,?, 'missing',0)
            """,
            (
                f"{index}.pdf",
                f"pdf/{index}.pdf",
                "pdf",
                f"https://files.example/{index}.pdf",
            ),
        )
    conn.commit()
    monkeypatch.setattr(
        assets,
        "download_asset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timeout")),
    )

    result = assets.recover_missing_assets(
        conn,
        tmp_path,
        max_assets=2,
        max_attempts=3,
        time_budget=10,
    )
    assert result["attempted"] == 2
    assert result["budget_exhausted"] is True
    assert conn.execute("SELECT COUNT(*) FROM asset_recovery_state").fetchone()[0] == 2


def test_jsonl_preserves_recoverable_asset_when_immediate_download_fails(monkeypatch, tmp_path):
    from mifp_app.services import importers

    conn = _connection()
    payload = {
        "type": "news",
        "data": {"title": "Asset recovery", "slug": "asset-recovery"},
        "assets": [
            {
                "url": "https://files.example/document.pdf",
                "kind": "pdf",
                "role": "document",
            }
        ],
    }
    jsonl = tmp_path / "records.jsonl"
    jsonl.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        importers,
        "download_asset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timeout")),
    )

    summary = importers.import_jsonl(conn, jsonl, assets_dir=tmp_path / "assets")
    asset = conn.execute(
        "SELECT source_url, storage_status, is_external FROM assets"
    ).fetchone()
    assert summary["linked_assets"] == 1
    assert dict(asset) == {
        "source_url": "https://files.example/document.pdf",
        "storage_status": "external",
        "is_external": 1,
    }
    assert conn.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 1
