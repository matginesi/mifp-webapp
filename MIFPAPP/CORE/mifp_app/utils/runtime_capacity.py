from __future__ import annotations

import math
import os
from pathlib import Path


def _positive_int(value: str | None) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _cgroup_cpu_limit(root: Path = Path("/sys/fs/cgroup")) -> int | None:
    """Return the CPU quota exposed by cgroup v2 or v1."""
    try:
        quota, period = (root / "cpu.max").read_text(encoding="ascii").split()[:2]
        if quota != "max":
            quota_value = _positive_int(quota)
            period_value = _positive_int(period)
            if quota_value and period_value:
                return max(1, math.ceil(quota_value / period_value))
    except (OSError, ValueError):
        pass

    try:
        quota_value = _positive_int((root / "cpu/cpu.cfs_quota_us").read_text(encoding="ascii").strip())
        period_value = _positive_int((root / "cpu/cpu.cfs_period_us").read_text(encoding="ascii").strip())
        if quota_value and period_value:
            return max(1, math.ceil(quota_value / period_value))
    except OSError:
        pass
    return None


def available_cpu_count(*, cgroup_root: Path = Path("/sys/fs/cgroup")) -> int:
    """Detect usable CPUs, respecting affinity and container CPU quotas."""
    candidates = [max(1, os.cpu_count() or 1)]
    try:
        candidates.append(max(1, len(os.sched_getaffinity(0))))
    except (AttributeError, OSError):
        pass
    cgroup_limit = _cgroup_cpu_limit(cgroup_root)
    if cgroup_limit:
        candidates.append(cgroup_limit)
    return min(candidates)


def configured_count(name: str, *, automatic: int, minimum: int = 1, maximum: int | None = None) -> int:
    """Read a positive integer override; missing/``auto`` uses detection."""
    raw = os.getenv(name, "auto").strip().lower()
    if raw in {"", "auto"}:
        value = automatic
    else:
        parsed = _positive_int(raw)
        if parsed is None:
            raise RuntimeError(f"{name} must be 'auto' or a positive integer")
        value = parsed
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def automatic_web_workers() -> int:
    # SQLite remains the primary store: use multiple processes, but cap write
    # contention and memory amplification on larger hosts.
    return max(1, min(available_cpu_count(), 4))


def automatic_background_workers() -> int:
    return max(1, min(available_cpu_count(), 2))
