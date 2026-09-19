#!/usr/bin/env python3
"""Bounded GHCR retention cleanup for the repository container package.

The script deliberately uses only the Python standard library so the scheduled
GitHub Actions cleanup does not depend on another JavaScript action/runtime.
It keeps the newest N versions, always protects every version tagged ``latest``,
and limits both pagination and deletions per run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

API_VERSION = "2022-11-28"
DEFAULT_API_URL = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
MAX_PAGES = 50
PER_PAGE = 100


class ApiError(RuntimeError):
    """A GitHub API request failed after bounded retry handling."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Version:
    id: int
    created_at: str
    tags: tuple[str, ...]

    @property
    def protects_latest(self) -> bool:
        return "latest" in self.tags


def _parse_created_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        # Unknown/legacy timestamps should not become preferred over versions
        # with a valid creation date.
        return datetime.min.replace(tzinfo=timezone.utc)


def _version_from_payload(payload: dict[str, Any]) -> Version:
    metadata = payload.get("metadata") or {}
    container = metadata.get("container") or {}
    tags = container.get("tags") or []
    return Version(
        id=int(payload["id"]),
        created_at=str(payload.get("created_at") or ""),
        tags=tuple(str(tag) for tag in tags if tag),
    )


def choose_versions_to_delete(
    versions: Iterable[Version],
    *,
    min_versions_to_keep: int,
    max_deletions: int,
) -> list[Version]:
    """Return old versions eligible for deletion, newest-retention first."""

    ordered = sorted(
        versions,
        key=lambda version: (_parse_created_at(version.created_at), version.id),
        reverse=True,
    )
    protected_ids = {version.id for version in ordered[:min_versions_to_keep]}
    protected_ids.update(version.id for version in ordered if version.protects_latest)

    candidates = [version for version in ordered if version.id not in protected_ids]
    # Delete the oldest candidates first. This makes partial/bounded runs useful.
    candidates.reverse()
    return candidates[:max_deletions]


class GitHubApi:
    def __init__(self, token: str, api_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, *, allow_404: bool = False) -> Any:
        url = f"{self.api_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "mifp-ghcr-retention",
            "X-GitHub-Api-Version": API_VERSION,
        }

        last_error: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            request = urllib.request.Request(url, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    if not body:
                        return None
                    return json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if allow_404 and exc.code == 404:
                    return None
                if exc.code not in {429, 500, 502, 503, 504}:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                    raise ApiError(exc.code, f"GitHub API {method} {path} failed: HTTP {exc.code}: {detail}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc

            if attempt + 1 < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))

        status = getattr(last_error, "code", None)
        raise ApiError(status, f"GitHub API {method} {path} failed after {MAX_RETRIES} attempts: {last_error}")

    def repository_owner_type(self, owner: str, repository: str) -> str:
        payload = self._request(
            "GET",
            f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(repository, safe='')}",
        )
        owner_type = str((payload.get("owner") or {}).get("type") or "")
        if owner_type not in {"Organization", "User"}:
            raise ApiError(None, f"Unsupported repository owner type: {owner_type or 'unknown'}")
        return owner_type

    @staticmethod
    def _package_base(owner: str, package_name: str, owner_type: str) -> str:
        owner_q = urllib.parse.quote(owner, safe="")
        package_q = urllib.parse.quote(package_name, safe="")
        if owner_type == "Organization":
            return f"/orgs/{owner_q}/packages/container/{package_q}"
        return f"/users/{owner_q}/packages/container/{package_q}"

    def list_versions(self, owner: str, package_name: str, owner_type: str) -> list[Version] | None:
        base = self._package_base(owner, package_name, owner_type)
        versions: list[Version] = []
        for page in range(1, MAX_PAGES + 1):
            payload = self._request(
                "GET",
                f"{base}/versions?per_page={PER_PAGE}&page={page}",
                allow_404=(page == 1),
            )
            if payload is None and page == 1:
                return None
            if not isinstance(payload, list):
                raise ApiError(None, "Unexpected GitHub package versions response")
            versions.extend(_version_from_payload(item) for item in payload)
            if len(payload) < PER_PAGE:
                return versions
        raise ApiError(None, f"Refusing to paginate beyond {MAX_PAGES * PER_PAGE} package versions")

    def delete_version(self, owner: str, package_name: str, owner_type: str, version_id: int) -> None:
        base = self._package_base(owner, package_name, owner_type)
        self._request("DELETE", f"{base}/versions/{version_id}")


def _parse_repository(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise argparse.ArgumentTypeError("repository must have the form owner/name")
    return parts[0], parts[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="GitHub repository as owner/name (default: GITHUB_REPOSITORY)",
    )
    parser.add_argument(
        "--package-name",
        default="",
        help="Container package name (default: repository name)",
    )
    parser.add_argument("--min-versions-to-keep", type=int, default=30)
    parser.add_argument("--max-deletions", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        owner, repository = _parse_repository(args.repository)
    except argparse.ArgumentTypeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.min_versions_to_keep < 5:
        print("ERROR: min-versions-to-keep must be at least 5", file=sys.stderr)
        return 2
    if not 1 <= args.max_deletions <= 500:
        print("ERROR: max-deletions must be between 1 and 500", file=sys.stderr)
        return 2

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("ERROR: GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    package_name = args.package_name.strip() or repository
    api_url = os.environ.get("GITHUB_API_URL", DEFAULT_API_URL)
    api = GitHubApi(token, api_url)

    try:
        owner_type = api.repository_owner_type(owner, repository)
        versions = api.list_versions(owner, package_name, owner_type)
        if versions is None:
            print(f"GHCR package {owner}/{package_name} does not exist yet; nothing to prune.")
            return 0

        deletions = choose_versions_to_delete(
            versions,
            min_versions_to_keep=args.min_versions_to_keep,
            max_deletions=args.max_deletions,
        )
        latest_count = sum(version.protects_latest for version in versions)
        print(
            f"GHCR versions={len(versions)} keep_newest={args.min_versions_to_keep} "
            f"latest_protected={latest_count} delete={len(deletions)}"
        )

        for version in deletions:
            tags = ",".join(version.tags) if version.tags else "<untagged>"
            if args.dry_run:
                print(f"DRY-RUN delete id={version.id} tags={tags} created={version.created_at}")
            else:
                api.delete_version(owner, package_name, owner_type, version.id)
                print(f"deleted id={version.id} tags={tags} created={version.created_at}")

        if len(deletions) == args.max_deletions:
            print("Deletion cap reached; any remaining old versions will be handled by a later run.")
        return 0
    except ApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
