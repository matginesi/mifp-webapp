"""Low-level filesystem helpers shared by archive and import code.

Archive extraction is the one place in the application that writes bytes whose
destination is derived from untrusted data.  The helpers here keep that write
inside the intended root even when the root already contains a symbolic link.
"""
from __future__ import annotations

import os
from typing import IO
from pathlib import Path


def open_write_no_follow(path: Path, *, mode: int = 0o640) -> IO[bytes]:
    """Open ``path`` for writing without following a symlink in the last part.

    A plain ``open(path, "wb")`` follows a pre-existing symlink, so an archive
    entry could be redirected to any file the application user can write even
    when the parent directory is correctly confined.  ``O_NOFOLLOW`` makes the
    open fail instead.  ``mode`` is applied at creation time so extracted
    content never exists with a more permissive mode than the runtime contract.

    ``O_NOFOLLOW`` is only meaningful on POSIX systems; elsewhere the flags
    degrade to an ordinary create/truncate open, which matches the previous
    behaviour rather than breaking extraction.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, mode)
    try:
        return os.fdopen(descriptor, "wb")
    except BaseException:
        os.close(descriptor)
        raise
