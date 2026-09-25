#!/usr/bin/env python3
"""Bounded cleanup of old completed GitHub Actions workflow runs.

The newest N completed runs of each workflow are always kept. Older runs are
only deleted once they are at least ``min_age_days`` old, so recent failures and
manual investigations remain available. The current workflow run is protected
explicitly even though it is normally still in progress and therefore absent
from the completed-run listing.
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
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

API_VERSION = "2022-11-28"
DEFAULT_API_URL = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
MAX_PAGES = 10
PER_PAGE = 100


class ApiError(RuntimeError):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class WorkflowRun:
    id: int
    workflow_id: int
    name: str
    created_at: str
    status: str


def _parse_created_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _run_from_payload(payload: dict[str, Any]) -> WorkflowRun:
    return WorkflowRun(
        id=int(payload["id"]),
        workflow_id=int(payload.get("workflow_id") or 0),
        name=str(payload.get("name") or payload.get("display_title") or "workflow"),
        created_at=str(payload.get("created_at") or ""),
        status=str(payload.get("status") or ""),
    )


def choose_runs_to_delete(
    runs: Iterable[WorkflowRun],
    *,
    keep_per_workflow: int,
    min_age_days: int,
    max_deletions: int,
    now: datetime | None = None,
    current_run_id: int | None = None,
) -> list[WorkflowRun]:
    """Return old completed runs eligible for deletion, oldest first."""

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    cutoff = reference.astimezone(timezone.utc) - timedelta(days=min_age_days)

    by_workflow: dict[int, list[WorkflowRun]] = defaultdict(list)
    for run in runs:
        if run.status != "completed" or run.id == current_run_id:
            continue
        by_workflow[run.workflow_id].append(run)

    candidates: list[WorkflowRun] = []
    for workflow_runs in by_workflow.values():
        ordered = sorted(
            workflow_runs,
            key=lambda run: (_parse_created_at(run.created_at), run.id),
            reverse=True,
        )
        for run in ordered[keep_per_workflow:]:
            if _parse_created_at(run.created_at) <= cutoff:
                candidates.append(run)

    candidates.sort(key=lambda run: (_parse_created_at(run.created_at), run.id))
    return candidates[:max_deletions]


class GitHubApi:
    def __init__(self, token: str, api_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str) -> Any:
        url = f"{self.api_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "mifp-actions-retention",
            "X-GitHub-Api-Version": API_VERSION,
        }
        last_error: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            request = urllib.request.Request(url, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    return json.loads(body.decode("utf-8")) if body else None
            except urllib.error.HTTPError as exc:
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

    def list_completed_runs(self, owner: str, repository: str) -> list[WorkflowRun]:
        owner_q = urllib.parse.quote(owner, safe="")
        repo_q = urllib.parse.quote(repository, safe="")
        runs: list[WorkflowRun] = []
        for page in range(1, MAX_PAGES + 1):
            payload = self._request(
                "GET",
                f"/repos/{owner_q}/{repo_q}/actions/runs?status=completed&per_page={PER_PAGE}&page={page}",
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
                raise ApiError(None, "Unexpected GitHub workflow-runs response")
            items = payload["workflow_runs"]
            runs.extend(_run_from_payload(item) for item in items)
            if len(items) < PER_PAGE:
                return runs
        raise ApiError(None, f"Refusing to paginate beyond {MAX_PAGES * PER_PAGE} completed workflow runs")

    def delete_run(self, owner: str, repository: str, run_id: int) -> None:
        owner_q = urllib.parse.quote(owner, safe="")
        repo_q = urllib.parse.quote(repository, safe="")
        self._request("DELETE", f"/repos/{owner_q}/{repo_q}/actions/runs/{run_id}")


def _parse_repository(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise argparse.ArgumentTypeError("repository must have the form owner/name")
    return parts[0], parts[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--keep-per-workflow", type=int, default=10)
    parser.add_argument("--min-age-days", type=int, default=14)
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
    if args.keep_per_workflow < 3:
        print("ERROR: keep-per-workflow must be at least 3", file=sys.stderr)
        return 2
    if args.min_age_days < 7:
        print("ERROR: min-age-days must be at least 7", file=sys.stderr)
        return 2
    if not 1 <= args.max_deletions <= 500:
        print("ERROR: max-deletions must be between 1 and 500", file=sys.stderr)
        return 2

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("ERROR: GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    current_run_raw = os.environ.get("GITHUB_RUN_ID", "").strip()
    current_run_id = int(current_run_raw) if current_run_raw.isdigit() else None
    api = GitHubApi(token, os.environ.get("GITHUB_API_URL", DEFAULT_API_URL))
    try:
        runs = api.list_completed_runs(owner, repository)
        deletions = choose_runs_to_delete(
            runs,
            keep_per_workflow=args.keep_per_workflow,
            min_age_days=args.min_age_days,
            max_deletions=args.max_deletions,
            current_run_id=current_run_id,
        )
        print(
            f"Actions completed_runs={len(runs)} keep_per_workflow={args.keep_per_workflow} "
            f"min_age_days={args.min_age_days} delete={len(deletions)}"
        )
        for run in deletions:
            if args.dry_run:
                print(f"DRY-RUN delete run={run.id} workflow={run.workflow_id} created={run.created_at}")
            else:
                api.delete_run(owner, repository, run.id)
                print(f"deleted run={run.id} workflow={run.workflow_id} created={run.created_at}")
        if len(deletions) == args.max_deletions:
            print("Deletion cap reached; remaining old runs will be handled by a later maintenance run.")
        return 0
    except ApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
