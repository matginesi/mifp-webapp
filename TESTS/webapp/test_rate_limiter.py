from __future__ import annotations

import pytest


def test_ip_rate_allowed_blocks_over_limit(tmp_path):
    from mifp_app.utils.security import ip_rate_allowed

    db = str(tmp_path / "rl.db")
    for _ in range(3):
        assert ip_rate_allowed("login", "1.2.3.4", limit=3, window_seconds=60, db_path=db) is True
    assert ip_rate_allowed("login", "1.2.3.4", limit=3, window_seconds=60, db_path=db) is False


def test_ip_rate_allowed_isolated_per_action_and_key(tmp_path):
    from mifp_app.utils.security import ip_rate_allowed

    db = str(tmp_path / "rl.db")
    assert ip_rate_allowed("login", "1.2.3.4", limit=1, window_seconds=60, db_path=db) is True
    assert ip_rate_allowed("login", "1.2.3.4", limit=1, window_seconds=60, db_path=db) is False
    assert ip_rate_allowed("join", "1.2.3.4", limit=1, window_seconds=60, db_path=db) is True
    assert ip_rate_allowed("login", "5.6.7.8", limit=1, window_seconds=60, db_path=db) is True


def test_ip_rate_allowed_expires_after_window(tmp_path):
    from mifp_app.utils.security import ip_rate_allowed

    db = str(tmp_path / "rl.db")
    for i in range(3):
        assert ip_rate_allowed("login", "1.2.3.4", limit=3, window_seconds=60, db_path=db, now=1000.0 + i) is True
    assert ip_rate_allowed("login", "1.2.3.4", limit=3, window_seconds=60, db_path=db, now=1061.0) is True


def test_ip_rate_allowed_disabled_when_limit_not_positive(tmp_path):
    from mifp_app.utils.security import ip_rate_allowed

    db = str(tmp_path / "rl.db")
    assert ip_rate_allowed("login", "1.2.3.4", limit=0, window_seconds=60, db_path=db) is True


def test_rate_limits_are_shared_across_processes(tmp_path):
    """A second store handle (same file) sees attempts from the first."""
    from mifp_app.utils.security import ip_rate_allowed

    db = str(tmp_path / "rl.db")
    assert ip_rate_allowed("admin_write", "admin:9.9.9.9", limit=2, window_seconds=60, db_path=db) is True
    assert ip_rate_allowed("admin_write", "admin:9.9.9.9", limit=2, window_seconds=60, db_path=db) is True
    assert ip_rate_allowed("admin_write", "admin:9.9.9.9", limit=2, window_seconds=60, db_path=db) is False


def test_reset_rate_limits_clears_store(tmp_path):
    from mifp_app.utils.security import ip_rate_allowed, reset_rate_limits

    db = str(tmp_path / "rl.db")
    assert ip_rate_allowed("join", "1.2.3.4", limit=1, window_seconds=3600, db_path=db) is True
    assert ip_rate_allowed("join", "1.2.3.4", limit=1, window_seconds=3600, db_path=db) is False

    assert reset_rate_limits(db_path=db) is True
    assert ip_rate_allowed("join", "1.2.3.4", limit=1, window_seconds=3600, db_path=db) is True
