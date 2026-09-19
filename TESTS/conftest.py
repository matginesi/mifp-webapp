import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# The application intentionally loads MIFPAPP/CORE/.env in normal operation.
# Tests must be deterministic and must not inherit local credentials, trusted
# hosts or storage paths from a developer workstation.
os.environ.setdefault("MIFP_LOAD_DOTENV", "0")

# Establish an isolated runtime before any test module can import the Flask
# Config class. Individual fixtures may override these values, but the fallback
# must never point at MIFPAPP/DATABASE or another developer-owned location.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEST_CACHE = Path(ROOT) / ".pytest_cache"
_TEST_CACHE.mkdir(exist_ok=True)
_TEST_RUNTIME = Path(tempfile.mkdtemp(prefix="mifp-runtime-", dir=_TEST_CACHE))
atexit.register(shutil.rmtree, _TEST_RUNTIME, ignore_errors=True)
for _name in ("assets", "exports", "logs", "conferences", "events", "config", "tmp"):
    (_TEST_RUNTIME / _name).mkdir()
os.environ["DATABASE_PATH"] = str(_TEST_RUNTIME / "mifp.db")
os.environ["ASSETS_DIR"] = str(_TEST_RUNTIME / "assets")
os.environ["EXPORT_DIR"] = str(_TEST_RUNTIME / "exports")
os.environ["LOG_DIR"] = str(_TEST_RUNTIME / "logs")
os.environ["CONFERENCES_DIR"] = str(_TEST_RUNTIME / "conferences")
os.environ["EVENTS_ROOT"] = str(_TEST_RUNTIME / "events")
os.environ["EVENTS_DOMAIN"] = "events.test"
os.environ["EVENTS_PHP_STATE_PATH"] = str(_TEST_RUNTIME / "events-php-enabled.txt")
os.environ["RUNTIME_CONFIG_DIR"] = str(_TEST_RUNTIME / "config")
os.environ["TMPDIR"] = str(_TEST_RUNTIME / "tmp")

for pkg in ("MIFPAPP/CORE", "SCRAPERS", "MIFPAPP/DATABASE/tools"):
    path = os.path.join(ROOT, pkg)
    if path not in sys.path:
        sys.path.insert(0, path)

# Freeze the process-wide Config only after the isolated defaults above are in
# place. Flask's Config class is imported once per pytest process; allowing the
# first arbitrary fixture to decide its paths makes later tests share that
# fixture's database and rate-limit store.
from mifp_app.config import Config as _TEST_CONFIG  # noqa: E402
from mifp_app.db.manage import init_database  # noqa: E402

# Runtime connections are intentionally forbidden from creating SQLite files.
# Tests therefore provision their process-wide fallback through the same
# explicit lifecycle used by local/production setup before any app is built.
init_database(_TEST_CONFIG.DATABASE_PATH)


@pytest.fixture(autouse=True)
def isolate_background_jobs():
    """Do not let process-global worker threads leak into another test database."""
    yield
    module = sys.modules.get("mifp_app.services.job_manager")
    if module is not None:
        module.reset_job_manager(wait=True)


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Clear the shared rate-limit store after every test.

    The store lives next to the per-test DATABASE_PATH, so attempts recorded by
    one test must not throttle (or leak into) later tests in the same run.
    """
    yield
    module = sys.modules.get("mifp_app.utils.security")
    if module is None:
        return
    candidates = {
        str(_TEST_RUNTIME / "mifp.db"),
        str(_TEST_CONFIG.DATABASE_PATH),
        os.environ.get("DATABASE_PATH", ""),
    }
    for database_path in candidates:
        if not database_path:
            continue
        try:
            module.reset_rate_limits(db_path=database_path)
        except Exception:
            continue
