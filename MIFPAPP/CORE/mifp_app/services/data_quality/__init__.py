"""Data Quality public API with lazy imports.

Importing lightweight helpers such as ``data_quality.normalizers`` must not
initialize the administrative executor, job machinery, or Flask-facing code.
"""
from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "analyze": ("analyzer", "analyze"),
    "count_findings": ("analyzer", "count_findings"),
    "count_workflows": ("analyzer", "count_workflows"),
    "finding_review_only": ("analyzer", "finding_review_only"),
    "get_finding": ("analyzer", "get_finding"),
    "latest_run": ("analyzer", "latest_run"),
    "list_findings": ("analyzer", "list_findings"),
    "manual_plan_actionable": ("analyzer", "manual_plan_actionable"),
    "cluster_is_safe": ("cluster", "cluster_is_safe"),
    "add_to_bundle": ("executor", "add_to_bundle"),
    "apply_bundle": ("executor", "apply_bundle"),
    "batch_add_to_bundle": ("executor", "batch_add_to_bundle"),
    "batch_reject_findings": ("executor", "batch_reject_findings"),
    "bundle_detail": ("executor", "bundle_detail"),
    "create_bundle": ("executor", "create_bundle"),
    "delete_draft": ("executor", "delete_draft"),
    "remove_from_bundle": ("executor", "remove_from_bundle"),
    "validate_bundle": ("executor", "validate_bundle"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value
