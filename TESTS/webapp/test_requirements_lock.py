from __future__ import annotations

import re
from pathlib import Path

_DIRECT_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*[<>=~!]")
_LOCK_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==")


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _webapp_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE"


def _read_requirement_names(path: Path, *, pinned_only: bool) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        pattern = _LOCK_NAME_RE if pinned_only else _DIRECT_NAME_RE
        match = pattern.match(line)
        if match:
            names.add(_normalize(match.group(1)))
    return names


def test_every_direct_requirement_is_pinned_in_lock():
    direct = _read_requirement_names(_webapp_dir() / "requirements.txt", pinned_only=False)
    pinned = _read_requirement_names(_webapp_dir() / "requirements.lock", pinned_only=True)

    assert direct, "requirements.txt contains no parseable direct dependencies"
    missing = sorted(direct - pinned)
    assert not missing, (
        "Direct requirements missing from requirements.lock: " + ", ".join(missing)
    )


def test_python_dotenv_is_a_runtime_dependency():
    direct = _read_requirement_names(_webapp_dir() / "requirements.txt", pinned_only=False)
    pinned = _read_requirement_names(_webapp_dir() / "requirements.lock", pinned_only=True)

    assert "python-dotenv" in direct
    assert "python-dotenv" in pinned
