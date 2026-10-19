"""Drain hook spool files into events (SCHEMA.md "Spool v1").

Each hook firing leaves `<b>.in` (raw stdin, may be invalid JSON) and then `<b>.json` (meta built
by sh, the commit marker). The CLI also writes `<b>.event.json` (a complete event) to a fallback
spool when the data dir is not writable (e.g. inside a Codex sandbox).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

from chainlog.paths import ensure_private_dir, safe_open

KEEP_KEYS = ("session_id", "transcript_path", "cwd", "hook_event_name", "reason", "source",
             "session_title", "model")
_SID = re.compile(r'"session_id"\s*:\s*"([A-Za-z0-9-]{8,64})"')
_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$")


def _all_dirs(rt):
    tmp = rt.env.get("TMPDIR") or "/tmp"
    return [rt.data_home / "spool", Path(tmp) / ("chainlog-spool-%d" % os.getuid())]


def trusted_dir(path, private=True) -> bool:
    """A real directory (not a symlink) owned by us; with private=True also no group/other access.
    The fallback spool lives in TMPDIR, which on Linux is the shared /tmp: anything else there could
    belong to another user. The main spool sits inside the 0700 data dir, so ownership suffices."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return False
    return not private or stat.S_IMODE(st.st_mode) & 0o077 == 0


def spool_dirs(rt) -> list:
    main, fallback = _all_dirs(rt)
    return [d for d, private in ((main, False), (fallback, True)) if trusted_dir(d, private)]


def fallback_dir(rt) -> Path:
    return _all_dirs(rt)[1]


def _read_json(path):
    try:
        with safe_open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _hook_event(meta, payload, basename) -> dict:
    harness = meta.get("h") if meta.get("h") in ("claude", "codex") else None
    if not harness:
        return None
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(sid, str) or not _ID_OK.match(sid):
        return None  # not an id (or carrying escapes): never let it become a session
    data = {k: payload.get(k) for k in KEEP_KEYS if k != "session_id" and payload.get(k) is not None}
    data.update({
        "anc": [a for a in (meta.get("anc") or []) if isinstance(a, int)],
        "tty": meta.get("tty") or "",
        "fp": {k: v for k, v in (meta.get("fp") or {}).items() if v},
        "chain": meta.get("chain") or "",
        "spool": basename,
    })
    etype = "hook.end" if meta.get("e") == "end" else "hook.start"
    return {
        "v": 1,
        "id": hashlib.sha256(basename.encode()).hexdigest()[:20],
        "ts": int(meta.get("t") or 0) * 1000,
        "type": etype,
        "ref": "%s:%s" % (harness, sid),
        "src": "hook:%s" % harness,
        "data": data,
    }


def _quarantine(path: Path):
    q = path.parent / "quarantine"
    try:
        if not os.path.lexists(q):
            os.mkdir(q, 0o700)
        if not trusted_dir(q, private=False):
            os.unlink(path)  # never move files into a directory we do not own
            return
        os.replace(path, q / path.name)
    except OSError:
        pass


def collect(rt, known_ids) -> tuple:
    """Returns (new_events, files_to_remove). Does not delete anything itself."""
    events, done = [], []
    now = time.time()
    for d in spool_dirs(rt):
        if not d.is_dir():
            continue
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            path = d / name
            if name.startswith("."):
                continue
            if name.endswith(".event.json"):
                ev = _read_json(path)
                from chainlog.store.events import _well_typed
                if _well_typed(ev):
                    if ev["id"] not in known_ids:
                        events.append(ev)
                        known_ids.add(ev["id"])
                    done.append(path)
                else:
                    _quarantine(path)
                continue
            if name.endswith(".json"):
                base = name[: -len(".json")]
                meta = _read_json(path)
                inp = d / (base + ".in")
                if not isinstance(meta, dict) or meta.get("v") != 1:
                    _quarantine(path)
                    continue
                raw = ""
                try:
                    with safe_open(inp) as f:
                        raw = f.read()
                except OSError:
                    pass
                try:
                    payload = json.loads(raw) if raw.strip() else {}
                except ValueError:
                    m = _SID.search(raw)  # truncated / invalid stdin: keep the fingerprint anyway
                    payload = {"session_id": m.group(1)} if m else {}
                ev = _hook_event(meta, payload if isinstance(payload, dict) else {}, base)
                if ev is None:
                    _quarantine(path)
                    if inp.exists():
                        _quarantine(inp)
                    continue
                if ev["id"] not in known_ids:
                    events.append(ev)
                    known_ids.add(ev["id"])
                done.extend([path, inp])
            elif name.endswith(".in"):
                # orphan: its commit marker never arrived
                if not (d / (name[:-3] + ".json")).exists():
                    try:
                        if now - path.stat().st_mtime > 3600:
                            _quarantine(path)
                    except OSError:
                        pass
        _clean_quarantine(d / "quarantine", now)
    events.sort(key=lambda e: (e["ts"], e["id"]))
    return events, done


def _clean_quarantine(q: Path, now):
    if not trusted_dir(q, private=False):
        return  # missing, a symlink, or not ours: never delete through it
    for name in os.listdir(q):
        p = q / name
        try:
            st = os.lstat(p)
            if stat.S_ISREG(st.st_mode) and now - st.st_mtime > 7 * 86400:
                os.unlink(p)
        except OSError:
            pass


def remove(paths):
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


def write_fallback_event(rt, ev) -> Path:
    d = fallback_dir(rt)
    if not os.path.lexists(d):
        os.mkdir(d, 0o700)
    if not trusted_dir(d):
        raise PermissionError("refusing untrusted fallback spool %s (not ours, a symlink, or shared)" % d)
    path = d / ("%d-%d-%s.event.json" % (int(time.time()), os.getpid(), ev["id"]))
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(ev, f)
    return path


def pending_due(rt):
    """Contents of spool/.due as int, or None."""
    try:
        with safe_open(rt.data_home / "spool" / ".due") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def set_due_min(rt, due, since=None):
    """Like set_due, but a due time a hook wrote after `since` (a session that ended while this
    sweep ran) is kept when it is earlier."""
    path = rt.data_home / "spool" / ".due"
    if since is not None:
        try:
            if os.stat(path).st_mtime >= since:
                cur = pending_due(rt)
                if cur is not None and (due is None or cur < due):
                    due = cur
        except OSError:
            pass
    set_due(rt, due)


def set_due(rt, due):
    d = ensure_private_dir(rt.data_home / "spool")
    path = d / ".due"
    if due is None:
        try:
            path.unlink()
        except OSError:
            pass
        return
    tmp = d / (".due.tmp.%d" % os.getpid())
    with open(tmp, "w") as f:
        f.write("%d\n" % int(due))
    os.replace(tmp, path)
