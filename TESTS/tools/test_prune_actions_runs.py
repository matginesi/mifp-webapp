from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "prune_actions_runs.py"
SPEC = importlib.util.spec_from_file_location("prune_actions_runs", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
WorkflowRun = MODULE.WorkflowRun
choose_runs_to_delete = MODULE.choose_runs_to_delete


def _run(run_id: int, workflow_id: int, day: int, status: str = "completed") -> WorkflowRun:
    return WorkflowRun(
        id=run_id,
        workflow_id=workflow_id,
        name=f"workflow-{workflow_id}",
        created_at=f"2026-09-{day:02d}T00:00:00Z",
        status=status,
    )


def test_cleanup_keeps_recent_runs_per_workflow_and_only_deletes_old_completed_runs() -> None:
    runs = [
        _run(101, 1, 1),
        _run(102, 1, 2),
        _run(103, 1, 3),
        _run(104, 1, 20),
        _run(201, 2, 1),
        _run(202, 2, 20),
        _run(203, 2, 21),
        _run(204, 2, 22),
        _run(999, 3, 1, "in_progress"),
    ]

    deleted = choose_runs_to_delete(
        runs,
        keep_per_workflow=3,
        min_age_days=7,
        max_deletions=100,
        now=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )

    assert [run.id for run in deleted] == [101, 201]


def test_cleanup_respects_age_current_run_and_global_deletion_cap() -> None:
    runs = [_run(i, 1, i) for i in range(1, 11)]

    deleted = choose_runs_to_delete(
        runs,
        keep_per_workflow=3,
        min_age_days=7,
        max_deletions=2,
        now=datetime(2026, 9, 25, tzinfo=timezone.utc),
        current_run_id=1,
    )

    assert [run.id for run in deleted] == [2, 3]
    assert 1 not in {run.id for run in deleted}
