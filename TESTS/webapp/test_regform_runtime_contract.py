from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "TESTS" / "fixtures" / "regform_contract" / "index.php"


def test_regform_uses_private_runtime_and_cannot_write_public_tree(tmp_path: Path) -> None:
    php = shutil.which("php")
    if php is None:
        pytest.skip("PHP CLI is not installed")

    public_regform = tmp_path / "public" / "PLMCN-2027" / "regform"
    public_regform.mkdir(parents=True)
    script = public_regform / "index.php"
    shutil.copyfile(FIXTURE, script)
    script.chmod(0o444)
    public_regform.chmod(0o555)
    public_regform.parent.chmod(0o555)

    private_root = tmp_path / "private"
    registrations = private_root / "registrations"
    uploads = private_root / "uploads"
    registrations.mkdir(parents=True, mode=0o700)
    uploads.mkdir(mode=0o700)
    private_root.chmod(0o700)

    env = os.environ.copy()
    env.update(
        {
            "MIFP_EVENTS_PRIVATE_DIR": str(private_root),
            "MIFP_REGISTRATION_DIR": str(registrations),
            "MIFP_UPLOAD_DIR": str(uploads),
            "MIFP_CONTRACT_WRITE_UPLOAD": "1",
        }
    )
    result = subprocess.run(
        [php, str(script)], env=env, text=True, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert (registrations / "contract-submission.json").is_file()
    assert (uploads / "contract-upload.txt").is_file()
    assert not (public_regform / "must-not-be-created.txt").exists()
    assert stat.S_IMODE(public_regform.stat().st_mode) == 0o555
    assert stat.S_IMODE(private_root.stat().st_mode) == 0o700


def test_bootstrap_assigns_public_and_private_event_permissions_to_distinct_users() -> None:
    bootstrap = (ROOT / "deploy" / "bootstrap-vps.sh").read_text(encoding="utf-8")

    assert 'install -d -o "$MIFP_UID" -g "$EVENTS_PUBLIC_GROUP" -m 0750 "$EVENTS_LOCAL_ROOT"' in bootstrap
    assert 'install -d -o "$EVENTS_PHP_USER" -g "$EVENTS_PHP_USER" -m 0700 "$EVENTS_PRIVATE_DIR"' in bootstrap
    assert 'install -d -o "$EVENTS_PHP_USER" -g "$EVENTS_PHP_USER" -m 0700 "$EVENTS_PRIVATE_DIR/$dir"' in bootstrap
