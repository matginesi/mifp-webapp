from __future__ import annotations

import hashlib


def shard_index(nodeid: str, total: int) -> int:
    """Return a stable shard index for a pytest nodeid."""
    if total < 1:
        raise ValueError("total must be >= 1")
    digest = hashlib.blake2b(nodeid.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % total
