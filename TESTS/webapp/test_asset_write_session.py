from __future__ import annotations

from pathlib import Path

import pytest

from mifp_app.services.assets import AssetWriteSession


def test_asset_write_session_rolls_back_only_files_it_created(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    existing = root / "image" / "existing.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("keep", encoding="utf-8")
    source = tmp_path / "new.txt"
    source.write_text("new", encoding="utf-8")
    target = root / "image" / "new.txt"

    with pytest.raises(RuntimeError):
        with AssetWriteSession(root) as session:
            assert session.install(source, target) is True
            raise RuntimeError("force DB rollback")

    assert existing.read_text(encoding="utf-8") == "keep"
    assert not target.exists()


def test_asset_write_session_never_overwrites_different_existing_bytes(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    target = root / "image" / "same-name.txt"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")
    source = tmp_path / "source.txt"
    source.write_text("new", encoding="utf-8")

    with AssetWriteSession(root) as session, pytest.raises(FileExistsError):
        session.install(source, target)

    assert target.read_text(encoding="utf-8") == "old"
