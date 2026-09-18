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


def test_account_rate_key_is_opaque_and_case_insensitive():
    """The per-account limiter must not store the login name itself, and must
    treat differently-cased submissions as the same account."""
    from mifp_app.routes.auth import _account_rate_key

    key = _account_rate_key("Admin")
    assert key == _account_rate_key("admin")
    assert key == _account_rate_key("  ADMIN  ")
    assert "admin" not in key
    assert len(key) == 32


def test_account_failure_bound_is_scoped_to_the_submitted_name(monkeypatch):
    """Failed attempts throttle one submitted name only: the limiter uses a
    dedicated action and an opaque per-name key, so guessing against one name
    cannot lock another, and the login name is never stored in the store."""
    from mifp_app import create_app
    from mifp_app.routes import auth

    app = create_app()
    app.config["TESTING"] = False
    app.config["LOGIN_ACCOUNT_MAX_ATTEMPTS"] = 2
    app.config["LOGIN_ACCOUNT_LOCKOUT_SECONDS"] = 900

    seen: list[tuple[str, str]] = []
    remaining = {"admin": 2, "someone-else": 2}

    def fake_ip_rate_allowed(action, key, *, limit, window_seconds, **kwargs):
        seen.append((action, key))
        assert limit == 2 and window_seconds == 900
        remaining[key] = remaining.get(key, 2) - 1
        return remaining[key] >= 0

    monkeypatch.setattr(auth, "ip_rate_allowed", fake_ip_rate_allowed)

    with app.test_request_context("/login"):
        assert auth._account_failure_allowed("admin") is True
        assert auth._account_failure_allowed("admin") is True
        assert auth._account_failure_allowed("admin") is False
        # A different submitted name has its own budget.
        assert auth._account_failure_allowed("someone-else") is True

    assert all(action == "login_account" for action, _ in seen)
    admin_key = auth._account_rate_key("admin")
    assert "admin" not in "".join(key for _, key in seen)
    assert admin_key in {key for _, key in seen}


def test_account_failure_bound_is_disabled_under_testing(monkeypatch):
    from mifp_app import create_app
    from mifp_app.routes import auth

    app = create_app()
    app.config["TESTING"] = True
    app.config["LOGIN_ACCOUNT_MAX_ATTEMPTS"] = 1

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("limiter must be bypassed under TESTING")

    monkeypatch.setattr(auth, "ip_rate_allowed", explode)
    with app.test_request_context("/login"):
        assert auth._account_failure_allowed("admin") is True

