from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

_JOB_HISTORY_SECONDS = 24 * 3600


@dataclass
class JobState:
    id: str
    name: str
    status: str
    submitted_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None


class JobQueueFull(RuntimeError):
    pass


def _jobs_store_path(db_path: str) -> Path:
    return Path(db_path).with_name("jobs.sqlite3")


def _load_job_history(db_path: str | None) -> dict[str, JobState]:
    if not db_path:
        return {}
    try:
        with sqlite3.connect(str(_jobs_store_path(db_path)), timeout=5) as conn:
            rows = conn.execute(
                "SELECT id, name, status, submitted_at, started_at, finished_at, error "
                "FROM jobs ORDER BY submitted_at DESC LIMIT 30"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return {}
    cutoff = time.time() - _JOB_HISTORY_SECONDS
    return {
        row[0]: JobState(
            id=row[0],
            name=row[1],
            status=row[2],
            submitted_at=row[3],
            started_at=row[4],
            finished_at=row[5],
            error=row[6],
        )
        for row in rows
        if row[3] >= cutoff
    }


def _persist_job(db_path: str | None, state: JobState) -> None:
    if not db_path:
        return
    try:
        with sqlite3.connect(str(_jobs_store_path(db_path)), timeout=5) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "id TEXT PRIMARY KEY, name TEXT, status TEXT, "
                "submitted_at REAL, started_at REAL, finished_at REAL, error TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    state.id,
                    state.name,
                    state.status,
                    state.submitted_at,
                    state.started_at,
                    state.finished_at,
                    state.error,
                ),
            )
    except (sqlite3.Error, OSError):
        pass


class JobManager:
    """Small bounded executor for in-process administrative jobs.

    Durable business state remains in SQLite; this registry exposes local
    worker activity, prevents unbounded thread creation, and best-effort
    mirrors finished job records to a sibling ``jobs.sqlite3`` so a multi-worker
    deployment can still surface recent job history.
    """

    def __init__(self, max_workers: int = 2, max_pending: int = 4, *, db_path: str | None = None):
        self.max_workers = max(1, max_workers)
        self.max_pending = max(self.max_workers, max_pending)
        self._db_path = db_path
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="mifp-job")
        self._capacity = threading.BoundedSemaphore(self.max_pending)
        self._lock = threading.RLock()
        self._jobs: dict[str, JobState] = {}

    def submit(self, name: str, callback: Callable[[], object]) -> tuple[str, Future]:
        if not self._capacity.acquire(blocking=False):
            raise JobQueueFull("The background job queue is full.")
        job_id = uuid.uuid4().hex
        state = JobState(job_id, name[:80], "queued", time.time())
        with self._lock:
            self._jobs[job_id] = state
        _persist_job(self._db_path, state)

        def run():
            with self._lock:
                state.status = "running"
                state.started_at = time.time()
            _persist_job(self._db_path, state)
            try:
                result = callback()
                with self._lock:
                    state.status = "completed"
                _persist_job(self._db_path, state)
                return result
            except Exception as exc:
                with self._lock:
                    state.status = "failed"
                    state.error = str(exc)[:300]
                _persist_job(self._db_path, state)
                raise
            finally:
                with self._lock:
                    state.finished_at = time.time()
                    _persist_job(self._db_path, state)
                    self._prune_locked()
                self._capacity.release()

        return job_id, self._executor.submit(run)

    def snapshot(self) -> dict:
        merged = dict(_load_job_history(self._db_path))
        with self._lock:
            merged.update(self._jobs)
            active = sum(item.status in {"queued", "running"} for item in self._jobs.values())
            jobs = sorted(merged.values(), key=lambda item: item.submitted_at, reverse=True)
        return {
            "pid": os.getpid(),
            "max_workers": self.max_workers,
            "max_pending": self.max_pending,
            "active": active,
            "jobs": [asdict(item) for item in jobs[:30]],
        }

    def _prune_locked(self) -> None:
        cutoff = time.time() - 3600
        stale = [
            job_id for job_id, state in self._jobs.items()
            if state.finished_at is not None and state.finished_at < cutoff
        ]
        for job_id in stale:
            self._jobs.pop(job_id, None)

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop accepting work and release executor threads."""
        self._executor.shutdown(wait=wait, cancel_futures=True)


_manager: JobManager | None = None
_manager_pid: int | None = None
_manager_lock = threading.Lock()


def get_job_manager(max_workers: int = 2, max_pending: int = 4, *, db_path: str | None = None) -> JobManager:
    global _manager, _manager_pid
    pid = os.getpid()
    with _manager_lock:
        if _manager is None or _manager_pid != pid:
            _manager = JobManager(max_workers=max_workers, max_pending=max_pending, db_path=db_path)
            _manager_pid = pid
        return _manager


def reset_job_manager(*, wait: bool = True) -> None:
    """Dispose the process-wide manager, primarily for app/test lifecycle boundaries."""
    global _manager, _manager_pid
    with _manager_lock:
        manager = _manager
        _manager = None
        _manager_pid = None
    if manager is not None:
        manager.shutdown(wait=wait)
