from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _prepare_launcher_tree(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy2(repo_root / "mifp", tmp_path / "mifp")
    (tmp_path / "MIFPAPP" / "CORE").mkdir(parents=True)
    (tmp_path / "MIFPAPP" / "DATABASE").mkdir(parents=True)
    (tmp_path / "SCRAPERS").mkdir()
    (tmp_path / "MIFPAPP" / "CORE" / "manage.py").write_text("", encoding="utf-8")


def test_help_exposes_only_local_start_modes(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    result = subprocess.run(
        ["bash", "mifp", "help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "./mifp init" in result.stdout
    assert "./mifp local" in result.stdout
    assert "./mifp docker-local" in result.stdout
    assert "./mifp production" not in result.stdout
    assert "./mifp hash" in result.stdout
    assert "docker dev" not in result.stdout
    assert "docker prod" not in result.stdout


def test_launcher_is_local_only_and_has_no_production_commands() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    launcher = (repo_root / "mifp").read_text(encoding="utf-8")

    assert "docker-local|docker) docker_local" in launcher
    assert "production|prod)" not in launcher
    assert "start_production" not in launcher
    assert "compose_production" not in launcher
    assert "compose_public" not in launcher
    assert "start_production" not in launcher


def test_scraper_command_is_forwarded(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    stub = tmp_path / "SCRAPERS" / "run_all.sh"
    stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "mifp", "scrape", "remote", "--threads", "4"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--scrapers remote --threads 4" in result.stdout


def test_database_command_is_forwarded(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    stub = tmp_path / "MIFPAPP" / "DATABASE" / "build.sh"
    stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "mifp", "database", "--fresh", "--skip-downloads"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--fresh --skip-downloads" in result.stdout


def test_test_suite_is_forwarded(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    stub = tmp_path / "test_all.sh"
    stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "mifp", "test", "scraper", "--", "-x", "-vv"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--suite scraper -- -x -vv" in result.stdout


def test_scraper_local_uses_configured_default_path(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    stub = tmp_path / "SCRAPERS" / "run_all.sh"
    stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "mifp", "scrape", "local"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--local-root /run/media/matteo/ARCHDISK/srv/http/mifp.eu" in result.stdout


def test_scraper_remote_uses_default_threads(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    stub = tmp_path / "SCRAPERS" / "run_all.sh"
    stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "mifp", "scrape", "remote"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--threads 16" in result.stdout


def test_admin_username_is_forwarded_without_password_generation_flags(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    manager = tmp_path / "MIFPAPP" / "CORE" / "manage.py"
    manager.write_text(
        "import sys\nprint('ARGS=' + ' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    example = tmp_path / "MIFPAPP" / "CORE" / ".env.example"
    example.write_text("SECRET_KEY='x'\nADMIN_USERNAME='admin'\nADMIN_PASSWORD_HASH='hash'\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "mifp", "admin", "--username", "matteo"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--username matteo" in result.stdout
    assert "--generate" not in result.stdout
    assert "--non-interactive" not in result.stdout


def test_admin_rejects_removed_noninteractive_generation_flags(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    manager = tmp_path / "MIFPAPP" / "CORE" / "manage.py"
    manager.write_text("import sys\nprint('unexpected')\n", encoding="utf-8")
    example = tmp_path / "MIFPAPP" / "CORE" / ".env.example"
    example.write_text("SECRET_KEY='x'\nADMIN_USERNAME='admin'\nADMIN_PASSWORD_HASH=''\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "mifp", "admin", "--generate"],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "Uso: ./mifp admin" in result.stderr


def test_clean_dry_run_never_targets_import_data(tmp_path: Path) -> None:
    _prepare_launcher_tree(tmp_path)
    (tmp_path / "IMPORT_DATA").mkdir()
    protected = tmp_path / "IMPORT_DATA" / "protected.jsonl"
    protected.write_text("{}\n", encoding="utf-8")
    (tmp_path / "SCRAPERS" / "OUTPUTS").mkdir()
    (tmp_path / "SCRAPERS" / "OUTPUTS" / "records.jsonl").write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "mifp", "clean", "all", "--dry-run"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SCRAPERS/OUTPUTS" in result.stdout
    assert "IMPORT_DATA" not in result.stdout
    assert protected.read_text(encoding="utf-8") == "{}\n"


def test_local_launcher_validates_runtime_imports_not_only_marker() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    launcher = (repo_root / "mifp").read_text(encoding="utf-8")

    assert "venv_runtime_ready" in launcher
    assert "import flask" in launcher
    assert "if ! venv_runtime_ready" in launcher
    assert "install_local_dependencies" in launcher


def test_browser_tests_use_standard_pytest_progress_output() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = (repo_root / "test_all.sh").read_text(encoding="utf-8")

    assert 'pytest TESTS/browser -q' not in runner
    assert 'pytest TESTS/browser "${PYTEST_ARGS[@]}"' in runner


def test_test_runner_isolates_persistent_paths_before_pytest() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = (repo_root / "test_all.sh").read_text(encoding="utf-8")

    pytest_call = runner.index('"$PYTHON_BIN" -m pytest')
    for assignment in (
        'export DATABASE_PATH="$TEST_RUNTIME_DIR/mifp.db"',
        'export ASSETS_DIR="$TEST_RUNTIME_DIR/assets"',
        'export EXPORT_DIR="$TEST_RUNTIME_DIR/exports"',
        'export CONFERENCES_DIR="$TEST_RUNTIME_DIR/conferences"',
        'export LOG_DIR="$TEST_RUNTIME_DIR/logs"',
    ):
        assert assignment in runner
        assert runner.index(assignment) < pytest_call


def test_zip_it_includes_source_and_excludes_generated_data(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copy2(repo_root / "zip_it.sh", tmp_path / "zip_it.sh")
    (tmp_path / "MIFPAPP" / "CORE").mkdir(parents=True)
    (tmp_path / "MIFPAPP" / "DATABASE" / "assets").mkdir(parents=True)
    (tmp_path / "SCRAPERS" / "OUTPUTS").mkdir(parents=True)
    (tmp_path / "TESTS").mkdir()
    (tmp_path / "IMPORT_DATA").mkdir()

    (tmp_path / "MIFPAPP" / "CORE" / "app.py").write_text("print('app')\n", encoding="utf-8")
    (tmp_path / "MIFPAPP" / "DATABASE" / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "MIFPAPP" / "DATABASE" / "mifp.db").write_bytes(b"database")
    (tmp_path / "MIFPAPP" / "DATABASE" / "assets" / "download.jpg").write_bytes(b"asset")
    (tmp_path / "SCRAPERS" / "scrape.py").write_text("print('scrape')\n", encoding="utf-8")
    (tmp_path / "SCRAPERS" / "OUTPUTS" / "records.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "TESTS" / "test_app.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (tmp_path / "IMPORT_DATA" / "private.jsonl").write_text("{}\n", encoding="utf-8")

    output_dir = tmp_path / "archives"
    result = subprocess.run(
        ["bash", "zip_it.sh", "--output", str(output_dir)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    import zipfile

    archive_path = next(output_dir.glob("mifp-codebase-*.zip"))
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    assert "MIFPAPP/CORE/app.py" in names
    assert "MIFPAPP/DATABASE/build.sh" in names
    assert "SCRAPERS/scrape.py" in names
    assert "TESTS/test_app.py" in names
    assert "MIFPAPP/DATABASE/mifp.db" not in names
    assert "MIFPAPP/DATABASE/assets/download.jpg" not in names
    assert "SCRAPERS/OUTPUTS/records.jsonl" not in names
    assert "IMPORT_DATA/private.jsonl" not in names
