from pathlib import Path


def test_core_prefix_is_not_duplicated(monkeypatch):
    from mifp_app import config

    monkeypatch.setenv("DATABASE_PATH", "CORE/data/mifp.db")

    resolved = config._path_from_config("db_path", "DATABASE_PATH")

    assert resolved == (config.BASE_DIR / "data" / "mifp.db").resolve()
    assert "CORE/CORE" not in resolved.as_posix()


def test_database_relative_path_can_live_outside_core(monkeypatch):
    from mifp_app import config

    monkeypatch.setenv("DATABASE_PATH", "../DATABASE/mifp.db")

    assert config._path_from_config("db_path", "DATABASE_PATH") == (
        Path(config.BASE_DIR).parent / "DATABASE" / "mifp.db"
    ).resolve()
