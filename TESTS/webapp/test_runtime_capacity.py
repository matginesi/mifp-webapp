from __future__ import annotations

from pathlib import Path

import pytest

from mifp_app.utils.runtime_capacity import _cgroup_cpu_limit, configured_count


def test_cgroup_v2_cpu_quota_is_rounded_up(tmp_path: Path) -> None:
    (tmp_path / "cpu.max").write_text("150000 100000\n", encoding="ascii")

    assert _cgroup_cpu_limit(tmp_path) == 2


def test_unlimited_cgroup_has_no_cpu_limit(tmp_path: Path) -> None:
    (tmp_path / "cpu.max").write_text("max 100000\n", encoding="ascii")

    assert _cgroup_cpu_limit(tmp_path) is None


def test_configured_count_supports_auto_override_and_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIFP_TEST_WORKERS", "auto")
    assert configured_count("MIFP_TEST_WORKERS", automatic=3, maximum=4) == 3

    monkeypatch.setenv("MIFP_TEST_WORKERS", "12")
    assert configured_count("MIFP_TEST_WORKERS", automatic=3, maximum=4) == 4


def test_configured_count_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIFP_TEST_WORKERS", "many")

    with pytest.raises(RuntimeError, match="positive integer"):
        configured_count("MIFP_TEST_WORKERS", automatic=2)
