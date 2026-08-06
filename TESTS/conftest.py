import os
import sys

import pytest

# The application intentionally loads MIFPAPP/CORE/.env in normal operation.
# Tests must be deterministic and must not inherit local credentials, trusted
# hosts or storage paths from a developer workstation.
os.environ.setdefault("MIFP_LOAD_DOTENV", "0")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for pkg in ("MIFPAPP/CORE", "SCRAPERS", "MIFPAPP/DATABASE/tools"):
    path = os.path.join(ROOT, pkg)
    if path not in sys.path:
        sys.path.insert(0, path)


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
    try:
        module.reset_rate_limits()
    except Exception:
        return
