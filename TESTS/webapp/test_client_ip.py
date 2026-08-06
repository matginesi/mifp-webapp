from __future__ import annotations

from collections import OrderedDict

import pytest


@pytest.fixture
def app(tmp_path):
    import os
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    yield app


def test_x_forwarded_for_present_but_health_still_works(app):
    resp = app.test_client().get("/health", headers={"X-Forwarded-For": "1.2.3.4"})
    assert resp.status_code == 200


def test_get_client_ip_trusts_remote_addr_when_no_proxy(app):
    app.config["TRUST_PROXY"] = False
    with app.test_request_context(headers={"X-Forwarded-For": "9.9.9.9"}):
        from mifp_app.utils.security import get_client_ip
        ip = get_client_ip()
        # With TRUST_PROXY=0, X-Forwarded-For is ignored → remote_addr is used
        assert ip != "9.9.9.9"


def test_get_client_ip_uses_proxyfix_normalized_remote_addr(app):
    app.config["TRUST_PROXY"] = True
    with app.test_request_context(
        headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"},
        environ_base={"REMOTE_ADDR": "10.0.0.1"},
    ):
        from mifp_app.utils.security import get_client_ip
        ip = get_client_ip()
        assert ip == "10.0.0.1"


def test_rate_limit_bucket_prunes_expired_and_caps_clients(monkeypatch):
    from mifp_app.utils import security

    monkeypatch.setattr(security.time, "time", lambda: 100.0)
    bucket = OrderedDict(
        [
            ("expired", [10.0]),
            ("active-1", [95.0]),
            ("active-2", [96.0]),
            ("active-3", [97.0]),
        ]
    )

    security.prune_ip_rate_bucket(bucket, 20.0, max_clients=2)

    assert bucket == OrderedDict([("active-2", [96.0]), ("active-3", [97.0])])
