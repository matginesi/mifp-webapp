from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_production_startup_fails_when_required_data_is_missing(tmp_path):
    webapp_dir = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(webapp_dir),
            "FLASK_ENV": "production",
            "FLASK_DEBUG": "0",
            "SECRET_KEY": "x" * 64,
            "DATABASE_PATH": str(tmp_path / "missing-mifp.db"),
            "ASSETS_DIR": str(tmp_path / "missing-assets"),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "LOG_DIR": str(tmp_path / "logs"),
            "LOG_ACCESS_ENABLED": "0",
            "TESTING": "0",
        }
    )
    env["MIFP_CONFIG"] = str(webapp_dir / "config" / "webapp.json")

    result = subprocess.run(
        [sys.executable, "-c", "from mifp_app import create_app; create_app()"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Production data is missing" in result.stderr
    assert "DATABASE_PATH" in result.stderr
    assert "ASSETS_DIR" in result.stderr
