"""events.jsonl: append-only log, one event per line (SCHEMA.md "Event v1"). Never rewritten."""
from __future__ import annotations

import json
import os
import uuid

from chainlog.paths import ensure_private_dir, safe_open

MAX_LINE = 65536   # every writer holds the flock, so long lines cannot interleave
NOTE_MAX = 8000    # the CLI trims notes to this and says so


def new_event(rt, etype, ref, src, data=None, eid=None) -> dict:
    return {
        "v": 1,
        "id": eid or uuid.uuid4().hex[:20],
        "ts": int(rt.now() * 1000),
        "type": etype,
        "ref": ref,
        "src": src,
        "data": data or {},
    }


def _well_typed(ev) -> bool:
    """A valid-JSON line with the wrong field types must never crash every command: skip it."""
    if not isinstance(ev, dict):
        return False
    if not isinstance(ev.get("id"), str) or not isinstance(ev.get("type"), str):
        return False
    if not isinstance(ev.get("ref"), str) or not isinstance(ev.get("ts", 0), (int, float)):
        return False
    if not isinstance(ev.get("data", {}), dict):
        ev["data"] = {}
    return True


def torn_lines(rt) -> int:
    """Number of unparseable lines in events.jsonl (for doctor)."""
    n = 0
    try:
        with safe_open(rt.data_home / "events.jsonl") as f:
            for line in f:
                if line.strip():
                    try:
                        json.loads(line)
                    except ValueError:
                        n += 1
    except OSError:
        pass
    return n


def read_events(rt) -> list:
    path = rt.data_home / "events.jsonl"
    out = []
    try:
        with safe_open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if _well_typed(ev):
                    out.append(ev)
    except FileNotFoundError:
        pass
    return out


def _encode(ev) -> str:
    line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
    if len(line.encode("utf-8", "replace")) > MAX_LINE:
        # keep the line atomic-sized: trim free text fields, never drop identity fields
        data = dict(ev.get("data") or {})
        for k in ("note", "text", "summary", "title"):
            if isinstance(data.get(k), str):
                data[k] = data[k][:NOTE_MAX] + "…"
        ev = dict(ev, data=data)
        line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
    return line


def append_events(rt, events):
    """Caller must hold the writer lock. One write() per line, then fsync."""
    if not events:
        return
    home = ensure_private_dir(rt.data_home)
    fd = os.open(str(home / "events.jsonl"), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        # a crash mid-write can leave a torn last line; start on a fresh line so it cannot swallow
        # the event we are about to write (read_events skips the torn fragment)
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            os.write(fd, b"\n")
        for ev in events:
            os.write(fd, (_encode(ev) + "\n").encode("utf-8", "replace"))
        os.fsync(fd)
    finally:
        os.close(fd)
