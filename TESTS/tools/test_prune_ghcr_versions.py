from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "prune_ghcr_versions.py"
SPEC = importlib.util.spec_from_file_location("prune_ghcr_versions", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
Version = MODULE.Version
choose_versions_to_delete = MODULE.choose_versions_to_delete
GitHubApi = MODULE.GitHubApi


def _v(version_id: int, day: int, *tags: str) -> Version:
    return Version(
        id=version_id,
        created_at=f"2026-09-{day:02d}T00:00:00Z",
        tags=tuple(tags),
    )


def test_cleanup_keeps_newest_versions_and_latest_even_if_old() -> None:
    versions = [_v(i, i) for i in range(1, 11)]
    versions[0] = _v(1, 1, "latest")

    deleted = choose_versions_to_delete(
        versions,
        min_versions_to_keep=5,
        max_deletions=100,
    )

    assert [version.id for version in deleted] == [2, 3, 4, 5]
    assert 1 not in {version.id for version in deleted}
    assert not ({6, 7, 8, 9, 10} & {version.id for version in deleted})


def test_cleanup_deletes_oldest_first_and_respects_cap() -> None:
    versions = [_v(i, i) for i in range(1, 11)]

    deleted = choose_versions_to_delete(
        versions,
        min_versions_to_keep=5,
        max_deletions=2,
    )

    assert [version.id for version in deleted] == [1, 2]


def test_package_api_paths_support_org_and_user_owners() -> None:
    assert GitHubApi._package_base("MIFP", "mifp-webapp", "Organization") == (
        "/orgs/MIFP/packages/container/mifp-webapp"
    )
    assert GitHubApi._package_base("matteo", "mifp web", "User") == (
        "/users/matteo/packages/container/mifp%20web"
    )
