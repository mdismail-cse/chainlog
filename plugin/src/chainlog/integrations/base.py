"""Change objects, diff rendering, backup + atomic apply, manifest-driven uninstall."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
from pathlib import Path

from chainlog.paths import atomic_write, ensure_private_dir, safe_open


def sha(data):
    if data is None:
        return None
    raw = data.encode("utf-8", "surrogateescape") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def read_text(path, rt):
    try:
        with safe_open(path, rt=rt, errors="surrogateescape", newline="") as f:  # byte-exact: keeps CRLF
            return f.read()
    except FileNotFoundError:
        return None


class Change:
    """A planned edit of one file. after=None deletes the file. command changes have path=None."""

    def __init__(self, component, description, path=None, before=None, after=None, harness=False,
                 command=None, mode=0o644, undo_command=None, display=None):
        self.component = component
        self.description = description
        self.path = Path(path) if path else None
        self.before = before
        self.after = after
        self.harness = harness  # path is under ~/.claude or ~/.codex
        self.command = command
        self.undo_command = undo_command
        self.display = display
        self.mode = mode

    @property
    def noop(self):
        return self.command is None and self.before == self.after

    def diff(self, rt=None):
        if self.command:
            return "$ " + (self.display or " ".join(self.command))
        a = (self.before or "").splitlines(keepends=True)
        b = (self.after or "").splitlines(keepends=True)
        name = str(self.path)
        if rt is not None and name.startswith(str(rt.home)):
            name = "~" + name[len(str(rt.home)):]
        lines = difflib.unified_diff(a, b, fromfile=name if self.before is not None else "/dev/null",
                                     tofile=name if self.after is not None else "/dev/null")
        return "".join(lines).rstrip("\n")

    def to_json(self):
        return {"component": self.component, "description": self.description,
                "path": str(self.path) if self.path else None, "command": self.command,
                "noop": self.noop}


def manifest_path(rt):
    return rt.data_home / "backups" / "manifest.json"


def load_manifest(rt):
    try:
        with safe_open(manifest_path(rt)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"v": 1, "entries": []}


def save_manifest(rt, m):
    ensure_private_dir(manifest_path(rt).parent)
    atomic_write(manifest_path(rt), json.dumps(m, indent=2) + "\n", rt=rt)


class ConflictError(RuntimeError):
    pass


def apply(rt, changes):
    """Apply changes in order. Returns list of result strings. Raises ConflictError (nothing more is
    written) if a file changed since it was planned."""
    results = []
    m = load_manifest(rt)
    backups = ensure_private_dir(rt.data_home / "backups")
    try:
        _apply_all(rt, changes, m, backups, results)
    finally:
        save_manifest(rt, m)  # also after a conflict: what was written must stay undoable
    return results


def _apply_all(rt, changes, m, backups, results):
    failed_parts = set()
    for ch in changes:
        if ch.component in failed_parts:
            results.append("skipped    %s (an earlier step of %s failed)" % (ch.description, ch.component))
            continue
        if ch.noop:
            results.append("unchanged  %s" % ch.description)
            continue
        if ch.command:
            rc, out, err = rt.run(ch.command, timeout=120)
            if rc != 0:
                results.append("FAILED     %s: %s" % (ch.description, " ".join((err or out).split())[:200]))
                failed_parts.add(ch.component)
            else:
                results.append("done       %s" % ch.description)
                if ch.undo_command and not any(e.get("undo_command") == ch.undo_command for e in m["entries"]):
                    m["entries"].append({"component": ch.component, "command": ch.command,
                                         "undo_command": ch.undo_command, "at": int(time.time())})
                    save_manifest(rt, m)
            continue
        current = read_text(ch.path, rt)
        if current != ch.before:
            raise ConflictError("%s changed while setup was running; nothing more was written. "
                                "Run setup again." % ch.path)
        backup = None
        try:
            if ch.before is not None:
                backup = backups / ("%d-%s.bak" % (int(time.time() * 1000), ch.path.name))
                atomic_write(backup, ch.before, rt=rt, mode=0o600)
            if ch.after is None:
                os.unlink(ch.path)
            else:
                ch.path.parent.mkdir(parents=True, exist_ok=True)
                exists = os.path.exists(ch.path)
                atomic_write(ch.path, ch.after, rt=rt, allow_harness_write=ch.harness,
                             mode=None if exists else ch.mode)
        except OSError as e:
            results.append("FAILED     %s: %s" % (ch.description, e))
            failed_parts.add(ch.component)
            continue
        m["entries"].append({"component": ch.component, "path": str(ch.path), "harness": ch.harness,
                             "before_sha": sha(ch.before), "after_sha": sha(ch.after),
                             "backup": str(backup) if backup else None, "at": int(time.time())})
        save_manifest(rt, m)
        results.append("written    %s" % ch.description)
