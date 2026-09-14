"""Small, framework-free helpers for stable data fingerprints."""
from __future__ import annotations

import hashlib
import json


def stable_fingerprint(entity_type: str, records: list[dict], *, action: str = "") -> str:
    """Hash material record content while ignoring runtime/ordering fields."""
    operational = {
        "id", "uid", "created_at", "updated_at", "sort_order", "source_order", "display_order"
    }
    material = [
        {
            key: value
            for key, value in sorted(row.items())
            if key not in operational
        }
        for row in sorted(
            records,
            key=lambda item: json.dumps(
                {key: value for key, value in item.items() if key not in operational},
                sort_keys=True,
                default=str,
            ),
        )
    ]
    return hashlib.sha256(
        json.dumps([entity_type, action, material], ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
