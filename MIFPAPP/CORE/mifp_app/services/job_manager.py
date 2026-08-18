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

from ..utils.logger import get_logger, log_event, log_exception

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


class JobCancelled(RuntimeError):
    """Raised by cooperative background jobs after a cancellation request."""



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




def _persisted_job_status(db_path: str | None, job_id: str) -> str | None:
    if not db_path:
        return None
    try:
        with sqlite3.connect(str(_jobs_store_path(db_path)), timeout=2) as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            return str(row[0]) if row else None
    except (sqlite3.Error, OSError):
        return None


def _request_persisted_cancel(db_path: str | None, job_id: str) -> bool:
    if not db_path:
        return False
    try:
        with sqlite3.connect(str(_jobs_store_path(db_path)), timeout=5) as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='cancel_requested' "
                "WHERE id=? AND status IN ('queued','running','cancel_requested')",
                (job_id,),
            )
            return cur.rowcount > 0
    except (sqlite3.Error, OSError):
        return False

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
    except (sqlite3.Error, OSError) as exc:
        log_event(
            get_logger("jobs"),
            "job.state_persist_failed",
            "Background job state could not be persisted",
            level="WARNING",
            job_id=state.id,
            job_name=state.name,
            job_status=state.status,
            error_type=type(exc).__name__,
        )


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
        self._cancel_events: dict[str, threading.Event] = {}

    def submit(self, name: str, callback: Callable[[Callable[[], bool]], object]) -> tuple[str, Future]:
        def wrapped(cancel_event: Callable[[], bool]) -> object:
            return callback(cancel_event)
        return self._submit(name, wrapped)

    def submit_cancellable(
        self, name: str, callback: Callable[[Callable[[], bool]], object]
    ) -> tuple[str, Future]:
        """Submit a cooperative job whose callback can poll cancellation safely."""
        return self._submit(name, callback)

    def _submit(
        self, name: str, callback: Callable[[Callable[[], bool]], object]
    ) -> tuple[str, Future]:
        if not self._capacity.acquire(blocking=False):
            raise JobQueueFull("The background job queue is full.")
        job_id = uuid.uuid4().hex
        state = JobState(job_id, name[:80], "queued", time.time())
        cancel_event = threading.Event()
        with self._lock:
            self._jobs[job_id] = state
            self._cancel_events[job_id] = cancel_event
        _persist_job(self._db_path, state)

        last_remote_poll = [0.0]

        def cancelled() -> bool:
            if cancel_event.is_set():
                return True
            if not self._db_path:
                return False
            now = time.monotonic()
            if now - last_remote_poll[0] < 0.25:
                return False
            last_remote_poll[0] = now
            if _persisted_job_status(self._db_path, job_id) == "cancel_requested":
                cancel_event.set()
                return True
            return False

        def run():
            with self._lock:
                if cancelled():
                    state.status = "cancelled"
                    state.started_at = state.started_at or time.time()
                    raise_cancelled = True
                else:
                    state.status = "running"
                    state.started_at = time.time()
                    raise_cancelled = False
            _persist_job(self._db_path, state)
            try:
                if raise_cancelled:
                    raise JobCancelled("Job cancelled before execution")
                result = callback(cancelled)
                if cancelled():
                    raise JobCancelled("Job cancelled")
                with self._lock:
                    state.status = "completed"
                _persist_job(self._db_path, state)
                return result
            except JobCancelled as exc:
                with self._lock:
                    state.status = "cancelled"
                    state.error = str(exc)[:300]
                _persist_job(self._db_path, state)
                return None
            except Exception as exc:
                with self._lock:
                    state.status = "failed"
                    state.error = str(exc)[:300]
                _persist_job(self._db_path, state)
                log_exception(
                    get_logger("jobs"),
                    "job.failed",
                    "Background job failed",
                    job_id=state.id,
                    job_name=state.name,
                    error_type=type(exc).__name__,
                )
                raise
            finally:
                with self._lock:
                    state.finished_at = time.time()
                    _persist_job(self._db_path, state)
                    self._prune_locked()
                self._capacity.release()

        return job_id, self._executor.submit(run)

    def request_cancel(self, job_id: str) -> bool:
        local = False
        with self._lock:
            state = self._jobs.get(job_id)
            cancel_event = self._cancel_events.get(job_id)
            if state is not None and cancel_event is not None and state.status in {"queued", "running", "cancel_requested"}:
                cancel_event.set()
                state.status = "cancel_requested"
                _persist_job(self._db_path, state)
                local = True
        # A cancellation HTTP request may land on another Gunicorn worker.
        # Persist the request so the owning worker observes it at its next safe
        # checkpoint even when this process has no in-memory JobState.
        remote = _request_persisted_cancel(self._db_path, job_id)
        return local or remote

    def snapshot(self) -> dict:
        merged = dict(_load_job_history(self._db_path))
        with self._lock:
            merged.update(self._jobs)
            active = sum(item.status in {"queued", "running", "cancel_requested"} for item in self._jobs.values())
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
            self._cancel_events.pop(job_id, None)

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
