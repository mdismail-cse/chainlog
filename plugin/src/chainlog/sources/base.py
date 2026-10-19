"""Shared helpers for read-only transcript parsing. parse() implementations never raise."""
from __future__ import annotations

import calendar
import json
import os
import re

from chainlog.paths import safe_open

HEAD = 64 * 1024
TAIL = 512 * 1024
FULL_MAX = 4 * 1024 * 1024
LINE_MAX = 8 * 1024 * 1024

_ISO = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?")


def iso_to_epoch(text):
    if not isinstance(text, str):
        return None
    m = _ISO.match(text)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
    frac = float("0." + m.group(7)) if m.group(7) else 0.0
    t = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) + frac
    tz = m.group(8)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        t -= sign * (hh * 3600 + mm * 60)
    return t


def file_state(path, parser_version):
    st = os.stat(path)
    return {"ino": st.st_ino, "size": st.st_size, "mtime": st.st_mtime, "pv": parser_version}


def iter_json_lines(path, size):
    """Yield (part, obj) for the head and tail of a JSONL file ('full' when small enough).

    Partial first/last lines of each window are skipped; overlong lines are skipped.
    """
    with safe_open(path, "rb") as f:
        if size <= FULL_MAX:
            windows = [("full", 0, size)]
        else:
            windows = [("head", 0, HEAD), ("tail", max(HEAD, size - TAIL), size)]
        for part, start, end in windows:
            f.seek(start)
            blob = f.read(end - start)
            lines = blob.split(b"\n")
            if part == "tail" and start > 0:
                lines = lines[1:]
            if part in ("head", "full") and end < size:
                lines = lines[:-1]
            if part in ("tail", "full") and blob and not blob.endswith(b"\n"):
                lines = lines[:-1]  # last line may still be being written
            for raw in lines:
                if not raw or len(raw) > LINE_MAX:
                    continue
                try:
                    # invalid UTF-8 must not hide a whole line (and with it a real prompt)
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield part, obj


def scan_markers(path, markers):
    """Stream the whole file, json-parsing only lines containing one of `markers`."""
    bmarkers = [m.encode() for m in markers]
    with safe_open(path, "rb") as f:
        for raw in f:
            if len(raw) > LINE_MAX or not any(m in raw for m in bmarkers):
                continue
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def merge_prev(facts, prev, keys):
    """Keep facts from an earlier parse when this (head+tail) parse could not see them."""
    if not prev:
        return facts
    for k in keys:
        if not facts.get(k) and prev.get(k):
            facts[k] = prev[k]
    if prev.get("prs"):
        seen = {p.get("url") or (p.get("repo"), p.get("number")) for p in facts.get("prs") or []}
        for p in prev["prs"]:
            key = p.get("url") or (p.get("repo"), p.get("number"))
            if key not in seen:
                facts.setdefault("prs", []).append(p)
    titles = dict(prev.get("titles") or {})
    titles.update({k: v for k, v in (facts.get("titles") or {}).items() if v})
    facts["titles"] = titles
    return facts
