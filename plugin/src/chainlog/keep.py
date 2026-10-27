"""Copies of the sessions you want to keep, so Claude's cleanup (cleanupPeriodDays) cannot take them.

A kept session is a gzip of its transcript in <data>/kept/claude/<id>.jsonl.gz with a small <id>.json beside it
(where the original lived and what it looked like when copied). Resuming a kept session whose original is gone puts
the copy back first. Only Claude sessions are kept: Codex does not delete its sessions.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
from pathlib import Path

from chainlog.paths import atomic_write, ensure_private_dir, safe_open

QUIET = 30  # seconds: a transcript changed more recently than this is being written, so copy it later
CHUNK = 1 << 20
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


class KeepError(Exception):
    """Something to tell the user in one sentence."""


def supported(s):
    return s["harness"] == "claude" and bool(_ID.match(s.get("native_id") or ""))


def files(rt, s):
    """(copy, details) paths for a session."""
    base = rt.data_home / "kept" / "claude" / s["native_id"]
    return Path(str(base) + ".jsonl.gz"), Path(str(base) + ".json")


def meta(rt, s):
    """What is known about the kept copy, or None when there is none (or it is incomplete)."""
    if not supported(s):
        return None
    gz, details = files(rt, s)
    try:
        with safe_open(details) as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) and os.path.isfile(gz) else None


def has_copy(rt, s):
    return meta(rt, s) is not None


def days_left(rt, s):
    """Days until Claude deletes the original (from the file's age and cleanupPeriodDays), or None."""
    from chainlog.sources.claude import retention_days
    path = s["transcript"].get("path")
    if s["harness"] != "claude" or not s["transcript"].get("present") or not path:
        return None
    try:
        age = (rt.now() - os.stat(path).st_mtime) / 86400.0
    except OSError:
        return None
    return retention_days(rt) - age


def keep(rt, s):
    """Copy the session's transcript. Returns the details that were stored. Raises KeepError."""
    if s["harness"] != "claude":
        raise KeepError("Codex does not delete its sessions, so there is nothing to keep.")
    if not supported(s):
        raise KeepError("this does not look like a Claude session id.")
    src = s["transcript"].get("path")
    if not s["transcript"].get("present") or not src or not os.path.isfile(src):
        raise KeepError("Claude has already deleted this transcript, so there is nothing left to copy.")
    gz, details = files(rt, s)
    ensure_private_dir(gz.parent)
    tmp = gz.with_name(".%s.tmp.%d" % (gz.name, os.getpid()))
    try:
        st = os.stat(src)
        with safe_open(src, "rb") as fin, open(tmp, "wb") as raw:
            # exactly the bytes there were when we looked: a session still writing cannot make the copy longer than its record
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as out:
                left = st.st_size
                while left > 0:
                    block = fin.read(min(CHUNK, left))
                    if not block:
                        break
                    out.write(block)
                    left -= len(block)
            raw.flush()
            os.fsync(raw.fileno())
        os.chmod(tmp, 0o600)
        m = {"v": 1, "ref": s["ref"], "native_id": s["native_id"], "orig_path": str(src), "cwd": s.get("cwd"),
             "size": st.st_size, "mtime": st.st_mtime, "copied_at": rt.now(), "gz_bytes": os.path.getsize(tmp)}
        os.replace(tmp, gz)
        atomic_write(details, json.dumps(m, indent=1) + "\n", rt=rt, mode=0o600)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise KeepError("could not write the copy: %s" % e)
    return m


def stale(rt, s):
    """The original has changed since the copy was made (and is not being written right now)."""
    m = meta(rt, s)
    path = s["transcript"].get("path")
    if m is None or not s["transcript"].get("present") or not path:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    return (st.st_size, st.st_mtime) != (m.get("size"), m.get("mtime")) and rt.now() - st.st_mtime > QUIET


def refresh(rt, sessions):
    """Bring the copies of kept sessions up to date. Returns (refreshed refs, [(ref, problem)])."""
    done, failed = [], []
    for s in sessions:
        if not s.get("kept") or s["harness"] != "claude" or not s["transcript"].get("present"):
            continue
        if has_copy(rt, s) and not stale(rt, s):
            continue
        if not has_copy(rt, s) and rt.now() - _mtime(s) <= QUIET:
            continue
        try:
            keep(rt, s)
            done.append(s["ref"])
        except KeepError as e:
            failed.append((s["ref"], str(e)))
    return done, failed


def _mtime(s):
    try:
        return os.stat(s["transcript"]["path"]).st_mtime
    except (OSError, KeyError, TypeError):
        return 0


def restore(rt, s):
    """Put the kept copy back where Claude looks for it. Returns the path, or None when the original is already there."""
    m = meta(rt, s)
    if m is None:
        raise KeepError("there is no kept copy of this session (chainlog keep makes one while Claude still has it).")
    dest = Path(str(m.get("orig_path") or ""))
    projects = Path(os.path.realpath(rt.claude_dir / "projects"))
    folder = Path(os.path.realpath(dest.parent))
    if dest.name != s["native_id"] + ".jsonl" or folder.parent != projects or os.path.islink(dest):
        raise KeepError("the kept copy points outside Claude's project folders (%s); not restoring it." % dest)
    if os.path.isfile(dest):
        return None
    gz, _details = files(rt, s)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(".%s.tmp.%d" % (dest.name, os.getpid()))
    try:
        with gzip.open(gz, "rb") as fin, safe_open(tmp, "wb", rt=rt, allow_harness_write=True) as out:
            shutil.copyfileobj(fin, out, CHUNK)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)  # a fresh modification time: Claude's cleanup clock starts again, so it is not swept at once
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise KeepError("could not restore the copy: %s" % e)
    return dest


def remove(rt, s):
    """Delete the kept copy. Returns the bytes freed."""
    freed = 0
    for p in files(rt, s) if supported(s) else ():
        try:
            freed += os.path.getsize(p)
            os.unlink(p)
        except OSError:
            pass
    return freed
