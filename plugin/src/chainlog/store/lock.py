"""Exclusive writer lock (flock). The hook never locks; readers never block on it."""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager

from chainlog.paths import ensure_private_dir


@contextmanager
def writer_lock(rt, timeout=5.0):
    """Yields True when the lock was acquired, False when it timed out or the data dir is not
    writable (e.g. inside a Codex sandbox) — the caller then degrades to in-memory / fallback."""
    try:
        home = ensure_private_dir(rt.data_home)
        fd = os.open(str(home / "lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        yield False
        return
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
