from __future__ import annotations

import atexit
import copy
import hashlib
import json
import logging
import logging.handlers
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, current_app, g, has_request_context, request, session

_listener: logging.handlers.QueueListener | None = None
_queue_handler: logging.Handler | None = None
_logging_signature: tuple[Any, ...] | None = None
_logging_pid: int | None = None
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_STANDARD_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__)
_SENSITIVE_PARTS = {
    "password", "passwd", "pwd", "secret", "secretkey", "token", "csrf",
    "authorization", "cookie", "session", "apikey", "privatekey",
    "smtppassword", "adminpassword", "accesstoken", "refreshtoken",
    "passwordhash",
}

_metric_buffer: dict[tuple[str, str, str, str], int] = {}
_metric_buffer_lock = threading.Lock()
_metric_db_path: str | None = None
_metric_flusher_thread: threading.Thread | None = None
_METRIC_FLUSH_INTERVAL_SECONDS = 5.0
_throttled_events: dict[str, float] = {}
_throttled_events_lock = threading.Lock()


def _metric_accumulate(db_path: str, scope: str, metric_name: str, metric_key: str) -> None:
    """Add one increment to the in-memory metric accumulator."""
    global _metric_db_path
    day = datetime.now(UTC).date().isoformat()
    with _metric_buffer_lock:
        _metric_db_path = db_path
        key = (day, scope, metric_name, metric_key or "")
        _metric_buffer[key] = _metric_buffer.get(key, 0) + 1
    _ensure_metric_flusher()


def _ensure_metric_flusher() -> None:
    global _metric_flusher_thread
    if _metric_flusher_thread is not None and _metric_flusher_thread.is_alive():
        return
    thread = threading.Thread(
        target=_metric_flusher_loop,
        name="mifp-metric-flusher",
        daemon=True,
    )
    thread.start()
    _metric_flusher_thread = thread


def _metric_flusher_loop() -> None:
    while True:
        time.sleep(_METRIC_FLUSH_INTERVAL_SECONDS)
        try:
            flush_metric_buffer()
        except Exception:
            pass


def flush_metric_buffer(db_path: str | None = None) -> int:
    """Write buffered metric increments to the database in one batch.

    Returns the number of aggregated entries written, or ``0`` when the buffer
    is empty, the database is unavailable, or the metrics table is missing.
    Idempotent: an empty buffer is a no-op and a second call flushes whatever
    accumulated in between.
    """
    with _metric_buffer_lock:
        if not _metric_buffer:
            return 0
        batch = dict(_metric_buffer)
        _metric_buffer.clear()
    path = db_path or _metric_db_path
    if not path:
        return 0
    try:
        with sqlite3.connect(path, timeout=0.5) as conn:
            conn.execute("PRAGMA busy_timeout=500")
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metrics_daily'"
            ).fetchone()
            if not exists:
                return 0
            for (day, scope, metric_name, metric_key), amount in batch.items():
                conn.execute(
                    """
                    INSERT INTO metrics_daily(date, scope, metric_name, metric_key, metric_value)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(date, scope, metric_name, metric_key)
                    DO UPDATE SET
                        metric_value = metric_value + excluded.metric_value,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (day, scope, metric_name, metric_key, amount),
                )
            conn.commit()
        return len(batch)
    except (sqlite3.Error, OSError) as exc:
        # A busy database must never break request handling.
        log_event(
            get_logger("metrics"),
            "metrics.write_failed",
            "Aggregate metric update failed",
            level="DEBUG",
            error_type=type(exc).__name__,
        )
        return 0


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _level(value: str | int | None) -> int:
    if isinstance(value, int):
        return value
    return getattr(logging, str(value or "INFO").upper(), logging.INFO)


def _sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return any(part in normalized for part in _SENSITIVE_PARTS)


def redact(value: Any, *, key: str | None = None, _depth: int = 0) -> Any:
    """Return a JSON-safe copy with recursively redacted secrets and e-mail addresses."""
    if key is not None and _sensitive_key(key):
        return "[REDACTED]"
    if _depth > 12:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _EMAIL_RE.sub("[REDACTED_EMAIL]", value[:4000])
    if isinstance(value, Mapping):
        return {str(k): redact(v, key=str(k), _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(redact(v, _depth=_depth + 1) for v in value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact(v, _depth=_depth + 1) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{type(value).__name__}:{len(value)} bytes>"
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return redact(str(value), _depth=_depth + 1)


def current_request_id() -> str:
    return _request_id.get()


def _new_request_id(candidate: str | None = None) -> str:
    candidate = (candidate or "").strip()
    return candidate if _REQUEST_ID_RE.fullmatch(candidate) else uuid.uuid4().hex


def _client_fingerprint() -> tuple[str | None, str | None]:
    if not has_request_context():
        return None, None
    from .security import get_client_ip

    ip = get_client_ip()
    include = _as_bool(current_app.config.get("LOG_INCLUDE_CLIENT_IP", False))
    should_hash = _as_bool(current_app.config.get("LOG_HASH_CLIENT_IP", True))
    if include:
        return ip, None
    if not should_hash or not ip:
        return None, None
    salt = str(current_app.config.get("SECRET_KEY", "mifp-log"))
    digest = hashlib.sha256(f"{salt}:{ip}".encode("utf-8", "replace")).hexdigest()[:16]
    return None, digest


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", current_request_id())
        record.endpoint = getattr(record, "endpoint", None)
        record.method = getattr(record, "method", None)
        record.path = getattr(record, "path", None)
        record.status = getattr(record, "status", None)
        record.duration_ms = getattr(record, "duration_ms", None)
        record.actor = getattr(record, "actor", None)
        record.client_ip = getattr(record, "client_ip", None)
        record.client_ip_hash = getattr(record, "client_ip_hash", None)
        stream = getattr(record, "stream", "application")
        record.stream = stream
        if stream == "application" and record.levelno >= logging.ERROR:
            record.stream = "errors"
        record.event = getattr(record, "event", None)
        if not record.event:
            if record.exc_info:
                record.event = "application.exception"
            elif record.levelno >= logging.ERROR:
                record.event = "application.error"
            elif record.levelno >= logging.WARNING:
                record.event = "application.warning"
            else:
                record.event = "application.message"
        record.extra_fields = redact(getattr(record, "extra_fields", {}))
        if has_request_context():
            record.endpoint = getattr(record, "endpoint", None) or request.endpoint
            record.method = getattr(record, "method", None) or request.method
            record.path = getattr(record, "path", None) or request.path
            record.actor = getattr(record, "actor", None) or session.get("admin_username")
            raw_ip, hashed_ip = _client_fingerprint()
            record.client_ip = getattr(record, "client_ip", None) or raw_ip
            record.client_ip_hash = getattr(record, "client_ip_hash", None) or hashed_ip
        return True


class _FlowFilter(logging.Filter):
    def __init__(self, *flows: str, maximum_level: int | None = None) -> None:
        super().__init__()
        self.flows = set(flows)
        self.maximum_level = maximum_level

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "stream", "application") not in self.flows:
            return False
        return self.maximum_level is None or record.levelno <= self.maximum_level


class _PreserveQueueHandler(logging.handlers.QueueHandler):
    """Keep exception tuples for structured formatting in the listener thread."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            # A saturated logging queue must not block request handling or lose
            # the fact that logging is impaired.
            try:
                sys.stderr.write("MIFP logging queue full; record dropped\n")
                sys.stderr.flush()
            except OSError:
                return


class IsoFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")


class JsonFormatter(IsoFormatter):
    def format(self, record: logging.LogRecord) -> str:
        exception_type = exception_message = stack_trace = None
        if record.exc_info:
            exc_type = record.exc_info[0]
            if exc_type is not None:
                exception_type = exc_type.__name__
            exception_message = redact(str(record.exc_info[1]))
            stack_trace = redact(self.formatException(record.exc_info))
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record), "level": record.levelname,
            "logger": record.name, "stream": getattr(record, "stream", "application"),
            "event": getattr(record, "event", None), "message": redact(record.getMessage()),
            "module": record.module, "class": getattr(record, "class_name", None),
            "function": record.funcName, "line": record.lineno,
            "request_id": getattr(record, "request_id", None),
            "endpoint": getattr(record, "endpoint", None), "method": getattr(record, "method", None),
            "path": getattr(record, "path", None), "status": getattr(record, "status", None),
            "duration_ms": getattr(record, "duration_ms", None), "actor": getattr(record, "actor", None),
            "client_ip": getattr(record, "client_ip", None),
            "client_ip_hash": getattr(record, "client_ip_hash", None),
            "exception_type": exception_type, "exception_message": exception_message,
            "stack_trace": stack_trace,
        }
        payload.update(redact(getattr(record, "extra_fields", {})))
        return json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False, default=str)


class TextFormatter(IsoFormatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record)} | {record.levelname:<8} | "
            f"{getattr(record, 'stream', 'application')} | {record.name} | "
            f"event={getattr(record, 'event', None) or '-'} rid={getattr(record, 'request_id', '-') or '-'} | "
            f"{redact(record.getMessage())}"
        )
        fields = redact(getattr(record, "extra_fields", {}))
        if fields:
            base += " | " + json.dumps(fields, ensure_ascii=False, default=str, sort_keys=True)
        if record.exc_info:
            base += "\n" + str(redact(self.formatException(record.exc_info)))
        return base


class ConsoleFormatter(logging.Formatter):
    """Compact console output; ANSI is used only when explicitly enabled."""

    COLORS = {
        logging.DEBUG: "\033[2m",
        logging.INFO: "\033[36m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    RESET = "\033[0m"

    def __init__(self, *, colors: bool = False) -> None:
        super().__init__()
        self.colors = colors

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        component = record.name.removeprefix("mifp.")[:18]
        level = f"{record.levelname:<8}"
        if self.colors:
            level = f"{self.COLORS.get(record.levelno, '')}{level}{self.RESET}"
        method = getattr(record, "method", None)
        path = getattr(record, "path", None)
        status = getattr(record, "status", None)
        duration = getattr(record, "duration_ms", None)
        request_bits = " ".join(str(value) for value in (method, path, status) if value not in (None, ""))
        if duration is not None:
            request_bits += f" {float(duration):.0f} ms"
        message = str(redact(record.getMessage())).replace("\r", " ").replace("\n", " ")
        line = f"{timestamp} {level} {component:<18} {request_bits or message}"
        if request_bits and message and message.lower() not in {"request completed", "request"}:
            line += f"  {message}"
        request_id = getattr(record, "request_id", None)
        if request_id and request_id != "-":
            line += f"  rid={request_id}"
        fields = redact(getattr(record, "extra_fields", {}))
        if fields:
            rendered = json.dumps(fields, ensure_ascii=False, default=str, sort_keys=True)
            line += "  " + rendered[:1200]
        if record.exc_info:
            line += "\n" + str(redact(self.formatException(record.exc_info)))
        return line


def _file_handler(path: Path, formatter: logging.Formatter, flow_filter: logging.Filter, max_bytes: int = 0, backup_count: int = 0) -> logging.Handler:
    handler: logging.Handler
    if max_bytes > 0:
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8", delay=False)
    else:
        handler = logging.handlers.WatchedFileHandler(path, encoding="utf-8", delay=False)
    handler.setFormatter(formatter)
    handler.addFilter(flow_filter)
    return handler


def setup_logging(
    log_dir: Path, level: str | int = "INFO", *, json_logs: bool = False,
    log_format: str | None = None, output: str = "files", max_bytes: int = 0,
    backup_count: int = 0, access_enabled: bool = True, audit_enabled: bool = True,
    security_enabled: bool = True, colors: str | bool = "auto",
) -> logging.Logger:
    """Configure isolated, queue-backed MIFP streams. File rotation is active when
    ``max_bytes`` is positive; otherwise falls back to WatchedFileHandler for external
    logrotate/Docker.
    """
    rotation = max_bytes > 0
    global _listener, _queue_handler, _logging_signature, _logging_pid
    mifp_logger = logging.getLogger("mifp")
    log_dir = Path(log_dir)
    numeric_level = _level(level)
    selected_format = (log_format or ("json" if json_logs else "text")).lower()
    requested_output = output.lower()
    if requested_output not in {"stdout", "files", "both"}:
        raise ValueError("LOG_OUTPUT must be stdout, files, or both")
    selected_output = requested_output
    signature = (
        str(log_dir.resolve()), numeric_level, selected_format, selected_output,
        bool(access_enabled), bool(audit_enabled), bool(security_enabled),
        int(max_bytes), int(backup_count), str(colors),
    )
    current_pid = os.getpid()
    if _listener is not None and signature == _logging_signature and _logging_pid == current_pid:
        return mifp_logger
    if _listener is not None:
        shutdown_logging()
    formatter: logging.Formatter = JsonFormatter() if selected_format == "json" else TextFormatter()
    if selected_format not in {"json", "text"}:
        raise ValueError("LOG_FORMAT must be json or text")

    q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=10_000)
    queue_handler = _PreserveQueueHandler(q)
    queue_handler.addFilter(RequestContextFilter())
    mifp_logger.handlers[:] = [queue_handler]
    mifp_logger.setLevel(numeric_level)
    mifp_logger.propagate = False
    package_logger = logging.getLogger("mifp_app")
    package_logger.handlers[:] = [queue_handler]
    package_logger.setLevel(numeric_level)
    package_logger.propagate = False
    _queue_handler = queue_handler

    handlers: list[logging.Handler] = []
    ext = "jsonl" if selected_format == "json" else "log"
    if selected_output in {"files", "both"}:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(_file_handler(log_dir / f"mifp_app.{ext}", formatter, _FlowFilter("application", maximum_level=logging.WARNING), max_bytes, backup_count))
        handlers.append(_file_handler(log_dir / f"errors.{ext}", formatter, _FlowFilter("errors"), max_bytes, backup_count))
        if access_enabled:
            handlers.append(_file_handler(log_dir / f"access.{ext}", formatter, _FlowFilter("access"), max_bytes, backup_count))
        if audit_enabled:
            handlers.append(_file_handler(log_dir / f"audit.{ext}", formatter, _FlowFilter("audit"), max_bytes, backup_count))
        if security_enabled:
            handlers.append(_file_handler(log_dir / f"security.{ext}", formatter, _FlowFilter("security"), max_bytes, backup_count))
    if selected_output in {"stdout", "both"}:
        console = logging.StreamHandler(sys.stdout)
        if isinstance(colors, bool):
            use_colors = colors
        else:
            color_mode = str(colors).strip().lower()
            if color_mode not in {"auto", "on", "off"}:
                raise ValueError("LOG_COLORS must be auto, on, or off")
            use_colors = color_mode == "on" or (
                color_mode == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ
            )
        console.setFormatter(formatter if selected_format == "json" else ConsoleFormatter(colors=use_colors))
        handlers.append(console)
    _listener = logging.handlers.QueueListener(q, *handlers, respect_handler_level=True)
    _listener.start()
    _logging_signature = signature
    _logging_pid = current_pid
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    atexit.register(shutdown_logging)
    return mifp_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("mifp") else f"mifp.{name}")


def _emit(logger: logging.Logger, level: int, event: str, message: str, stream: str, fields: Mapping[str, Any], *, exc_info: Any = None) -> None:
    sanitized = dict(redact(fields))
    for key in list(sanitized):
        if key.lower() in {"ip", "source_ip", "client_ip", "remote_addr"}:
            sanitized.pop(key, None)
    logger.log(level, message, exc_info=exc_info, extra={"event": event, "stream": stream, "extra_fields": sanitized})


def log_event(logger: logging.Logger, event: str, message: str, *, level: str | int = "INFO", **fields: Any) -> None:
    _emit(logger, _level(level), event, message, "application", fields)


def log_event_throttled(
    logger: logging.Logger,
    event: str,
    message: str,
    *,
    interval_seconds: float = 60,
    throttle_key: str = "",
    level: str | int = "WARNING",
    **fields: Any,
) -> bool:
    """Emit a recurring operational problem at a bounded rate.

    Returns ``True`` when a record was emitted. This is intended for degraded
    fallbacks that may execute on every request or progress tick.
    """
    key = f"{logger.name}:{event}:{throttle_key}"
    now = time.monotonic()
    with _throttled_events_lock:
        previous = _throttled_events.get(key, 0.0)
        if now - previous < max(0.0, float(interval_seconds)):
            return False
        _throttled_events[key] = now
    log_event(logger, event, message, level=level, **fields)
    return True


def audit_event(
    event: str,
    message: str,
    *,
    outcome: str = "success",
    severity: str | None = None,
    **fields: Any,
) -> None:
    if severity is None:
        severity = (
            "warning"
            if outcome in {"failure", "failed", "denied", "rejected", "cancelled"}
            else "info"
        )
    _emit(get_logger("audit"), _level(severity), event, message, "audit", {"outcome": outcome, **fields})


def audit_log(
    event_type: str,
    action: str,
    *,
    category: str = "system",
    severity: str | None = None,
    outcome: str = "success",
    **fields: Any,
) -> None:
    """Backward-compatible alias for the documented audit API."""
    audit_event(event_type, action, outcome=outcome, severity=severity, category=category, **fields)


def security_event(event_type: str, action: str, *, outcome: str = "failure", severity: str = "warning", **fields: Any) -> None:
    _emit(get_logger("security"), _level(severity), event_type, action, "security", {"outcome": outcome, **fields})


def log_exception(logger: logging.Logger, event: str, message: str, **fields: Any) -> None:
    _emit(logger, logging.ERROR, event, message, "errors", fields, exc_info=True)


def shutdown_logging() -> None:
    global _listener, _queue_handler, _logging_signature, _logging_pid
    if _listener is not None:
        _listener.stop()
        _listener = None
    logger = logging.getLogger("mifp")
    if _queue_handler in logger.handlers:
        logger.removeHandler(_queue_handler)
    _queue_handler = None
    package_logger = logging.getLogger("mifp_app")
    package_logger.handlers[:] = []
    package_logger.propagate = True
    _logging_signature = None
    _logging_pid = None


def init_request_logging(app: Flask, db_path: str | None = None) -> None:
    access_logger = get_logger("access")

    @app.before_request
    def _start_request_timer() -> None:
        rid = _new_request_id(request.headers.get("X-Request-ID"))
        _request_id.set(rid)
        g.request_id = rid
        g.request_started_at = time.perf_counter()
        g.request_initial_flash_count = len(session.get("_flashes") or [])
        g.csp_nonce = uuid.uuid4().hex

    def _record_metrics(status_code: int, duration_ms: float) -> None:
        if not db_path or not app.config.get("PRIVACY_SAFE_METRICS_ENABLED", True):
            return
        if getattr(g, "maintenance_active", False):
            return
        if request.method not in {"GET", "HEAD"}:
            return
        path = request.path or "/"
        if path == "/favicon.ico" or path.startswith(("/dashboard", "/login", "/static", "/media", "/health", "/ready")):
            return
        # Honor a runtime-overridden DATABASE_PATH (e.g. tests that point the
        # app at a temporary database after create_app).
        target_db = str(app.config.get("DATABASE_PATH") or db_path)
        try:
            from ..services.metrics_service import increment_daily, normalize_metric_path, response_time_bucket
            normalized = normalize_metric_path(path)
            if app.config.get("TESTING"):
                # Keep tests deterministic: write synchronously exactly as before.
                with sqlite3.connect(target_db, timeout=0.1) as conn:
                    conn.execute("PRAGMA busy_timeout=100")
                    increment_daily(conn, "public_site", f"http_{status_code}", normalized)
                    if status_code == 200:
                        increment_daily(conn, "public_site", "page_view", normalized)
                    if status_code == 404:
                        increment_daily(conn, "technical", "http_404", normalized)
                    if status_code >= 500:
                        increment_daily(conn, "technical", "http_5xx", normalized)
                    increment_daily(conn, "technical", "response_time_bucket", f"{normalized}:{response_time_bucket(duration_ms)}")
            else:
                # Production: accumulate in memory; the background flusher writes
                # the batch so request handling never opens a database.
                _metric_accumulate(target_db, "public_site", f"http_{status_code}", normalized)
                if status_code == 200:
                    _metric_accumulate(target_db, "public_site", "page_view", normalized)
                if status_code == 404:
                    _metric_accumulate(target_db, "technical", "http_404", normalized)
                if status_code >= 500:
                    _metric_accumulate(target_db, "technical", "http_5xx", normalized)
                _metric_accumulate(target_db, "technical", "response_time_bucket", f"{normalized}:{response_time_bucket(duration_ms)}")
        except (sqlite3.Error, OSError) as exc:
            log_event(get_logger("metrics"), "metrics.write_failed", "Aggregate metric update failed", level="DEBUG", error_type=type(exc).__name__)

    @app.after_request
    def _log_request(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", current_request_id())
        started = getattr(g, "request_started_at", None)
        duration = round((time.perf_counter() - started) * 1000, 2) if started else 0.0
        fields = {
            "endpoint": request.endpoint,
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": duration,
            "request_bytes": request.content_length,
            "response_bytes": response.calculate_content_length(),
            # Names are useful for diagnosis; values may contain personal data
            # or credentials and must never enter the access log.
            "query_keys": sorted(request.args.keys())[:30],
        }
        flashes = session.get("_flashes") or []
        initial_flash_count = int(getattr(g, "request_initial_flash_count", 0) or 0)
        new_flashes = flashes[initial_flash_count:]
        error_feedback = any(
            isinstance(item, (list, tuple))
            and item
            and str(item[0]).casefold() in {"error", "danger"}
            for item in new_flashes
        )
        if error_feedback:
            fields["error_feedback"] = True
        quiet = request.path.startswith("/static/") or request.path in {"/health", "/ready"}
        if app.config.get("LOG_ACCESS_ENABLED", True) and not quiet:
            expected_maintenance = (
                response.status_code == 503
                and getattr(g, "maintenance_active", False)
            )
            if expected_maintenance:
                level = logging.INFO
                event = "request.maintenance"
                fields["expected_response"] = True
            else:
                level = (
                    logging.ERROR if response.status_code >= 500
                    else logging.WARNING if response.status_code >= 400
                    else logging.INFO
                )
                event = "request.failed" if response.status_code >= 400 else "request.completed"
            _emit(access_logger, level, event, "Request completed", "access", fields)
        threshold = float(app.config.get("LOG_SLOW_REQUEST_MS", 5000))
        if threshold > 0 and duration >= threshold and not quiet:
            _emit(access_logger, logging.WARNING, "request.slow", "Slow request", "access", fields)

        sensitive_area = request.path in {"/login", "/logout"} or request.path.startswith("/dashboard")
        if sensitive_area and (response.status_code >= 400 or error_feedback):
            area = "dashboard" if request.path.startswith("/dashboard") else "auth"
            log_event(
                get_logger(area),
                f"{area}.request_failed",
                "Administrative request failed",
                level="ERROR" if response.status_code >= 500 else "WARNING",
                endpoint=request.endpoint,
                method=request.method,
                path=request.path,
                status=response.status_code,
                duration_ms=duration,
                request_bytes=request.content_length,
                error_feedback=error_feedback,
            )

        # Coverage safety net: every state-changing dashboard request gets an
        # audit record even when an individual route forgets its domain event.
        # Submitted values are intentionally never included.
        if request.path.startswith("/dashboard") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if error_feedback:
                outcome = "failure"
            elif response.status_code < 400:
                outcome = "success"
            elif response.status_code in {401, 403, 429}:
                outcome = "denied"
            else:
                outcome = "failure"
            audit_event(
                "dashboard.mutation",
                "Dashboard state-changing request completed",
                outcome=outcome,
                severity="error" if response.status_code >= 500 else None,
                category="dashboard",
                endpoint=request.endpoint,
                method=request.method,
                path=request.path,
                status=response.status_code,
                duration_ms=duration,
                request_bytes=request.content_length,
                error_feedback=error_feedback,
            )
        _record_metrics(response.status_code, duration)
        return response


def _cleanup_table(db_path: str, table: str, date_column: str, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                return 0
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE datetime({date_column}) < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
            return max(cursor.rowcount, 0)
    except sqlite3.Error:
        return 0


def cleanup_page_views(db_path: str, retention_days: int = 365) -> int:
    return _cleanup_table(db_path, "page_views", "created_at", retention_days)


def cleanup_metrics_daily(db_path: str, retention_days: int = 730) -> int:
    return _cleanup_table(db_path, "metrics_daily", "date", retention_days)
