"""Framework-free service exceptions shared across runtime helpers."""
from __future__ import annotations


class JobQueueFull(RuntimeError):
    """Raised when the bounded background-job queue has no free slot."""


class JobCancelled(RuntimeError):
    """Raised by cooperative jobs after a cancellation request."""
