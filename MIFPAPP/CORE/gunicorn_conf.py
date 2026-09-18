from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Honour the same kill switch as ``mifp_app.config``: production Compose sets
# MIFP_LOAD_DOTENV=0 so a bind-mounted .env can never inject Gunicorn settings.
if os.getenv("MIFP_LOAD_DOTENV", "1").strip().lower() in {"1", "true", "yes", "on"}:
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

from mifp_app.utils.runtime_capacity import configured_count

bind = os.getenv("GUNICORN_BIND", f"{os.getenv('FLASK_HOST', '0.0.0.0')}:{os.getenv('FLASK_PORT', '8000')}")
# SQLite supports concurrent readers but serializes writes. One process avoids
# independent connection pools competing for the same WAL during admin work.
workers = configured_count("GUNICORN_WORKERS", automatic=1, maximum=2)
worker_class = "gthread"
threads = configured_count("GUNICORN_THREADS", automatic=4, maximum=8)
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = 5
accesslog = None  # application request logger owns access events
errorlog = "-"
capture_output = True
preload_app = False
reload = False
