from concurrent.futures import Future
from threading import Event

import pytest

from mifp_app.services.job_manager import (
    JobManager,
    JobQueueFull,
    get_job_manager,
    reset_job_manager,
)


def test_job_manager_bounds_running_and_pending_work():
    manager = JobManager(max_workers=1, max_pending=1)
    release = Event()
    _job_id, future = manager.submit("blocking-job", lambda: release.wait(2))

    with pytest.raises(JobQueueFull):
        manager.submit("rejected-job", lambda: None)

    assert manager.snapshot()["active"] == 1
    release.set()
    assert future.result(timeout=2) is True
    assert manager.snapshot()["jobs"][0]["status"] == "completed"


def test_job_manager_records_failure_without_exposing_unbounded_error():
    manager = JobManager(max_workers=1, max_pending=1)

    def fail():
        raise RuntimeError("x" * 500)

    _job_id, future = manager.submit("failed-job", fail)
    with pytest.raises(RuntimeError):
        future.result(timeout=2)

    state = manager.snapshot()["jobs"][0]
    assert state["status"] == "failed"
    assert len(state["error"]) == 300


def test_global_job_manager_can_be_cleanly_recreated():
    first = get_job_manager(max_workers=1, max_pending=1)

    reset_job_manager()
    second = get_job_manager(max_workers=2, max_pending=2)

    assert second is not first
    assert second.snapshot()["max_workers"] == 2


def test_job_state_persists_across_manager_instances(tmp_path):
    db_path = str(tmp_path / "app.db")
    store = tmp_path / "jobs.sqlite3"

    first = JobManager(max_workers=1, max_pending=1, db_path=db_path)
    _job_id, future = first.submit("durable-job", lambda: None)
    assert future.result(timeout=2) is None
    assert store.exists()

    second = JobManager(max_workers=1, max_pending=1, db_path=db_path)
    jobs = second.snapshot()["jobs"]
    assert any(j["name"] == "durable-job" and j["status"] == "completed" for j in jobs)


def test_job_persistence_is_best_effort(tmp_path):
    manager = JobManager(max_workers=1, max_pending=1, db_path=str(tmp_path / "no.db"))
    _job_id, future = manager.submit("lost-job", lambda: None)
    assert future.result(timeout=2) is None
    assert manager.snapshot()["jobs"][0]["status"] == "completed"
