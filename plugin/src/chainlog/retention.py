"""`chainlog retention`: how long Claude Code keeps transcripts (cleanupPeriodDays in ~/.claude/settings.json)."""
from __future__ import annotations

import json
import os
import re
import time

from chainlog.paths import atomic_write, ensure_private_dir, safe_open

KEY = "cleanupPeriodDays"
DEFAULT = 30  # Claude Code's own default
MAXIMUM = 3650


def path(rt):
    return rt.claude_dir / "settings.json"


def read(rt):
    """The text of settings.json, or None when there is none."""
    try:
        with safe_open(path(rt), errors="surrogateescape", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return None


def current(text):
    """The days written in the file, or None when it sets none (or something that is not a positive number)."""
    try:
        v = json.loads(text or "{}").get(KEY)
    except (ValueError, AttributeError):
        return None
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def edit(text, days):
    """settings.json text with cleanupPeriodDays set to `days` (removed when None). Touches nothing else when it can:
    a plain text edit, checked by parsing the result; the file is rewritten as JSON only if that check fails."""
    data = json.loads(text) if text and text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("settings.json is not a JSON object")
    want = dict(data)
    if days is None:
        want.pop(KEY, None)
    else:
        want[KEY] = days
    if want == data:
        return text
    new = None
    num = re.compile(r'("%s"\s*:\s*)-?\d+(?:\.\d+)?' % KEY)
    if text and text.strip():
        if days is not None and KEY in data:
            new = num.sub(lambda m: m.group(1) + str(days), text, count=1)
        elif days is None:
            new = re.sub(r'[ \t]*"%s"\s*:\s*-?\d+(?:\.\d+)?\s*,?[ \t]*\n?' % KEY, "", text, count=1)
        elif data:
            i = text.index("{")
            m = re.search(r'\{[ \t]*\r?\n([ \t]+)"', text)
            new = text[:i + 1] + '\n%s"%s": %d,' % (m.group(1) if m else "  ", KEY, days) + text[i + 1:]
    if new is None or _parse(new) != want:
        new = json.dumps(want, indent=2, ensure_ascii=False) + "\n"
    return new


def _parse(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def write(rt, before, after):
    """Back the old file up (in chainlog's data folder), then write the new one. Returns the backup path or None."""
    backup = None
    if before is not None:
        folder = ensure_private_dir(rt.data_home / "backups")
        backup = folder / ("%d-settings.json.bak" % int(time.time() * 1000))
        atomic_write(backup, before, rt=rt, mode=0o600)
    target = path(rt)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, after, rt=rt, allow_harness_write=True, mode=None if os.path.exists(target) else 0o644)
    return backup
