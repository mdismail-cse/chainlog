"""Resume a session in its own folder with its own harness."""
from __future__ import annotations

import os
import shlex

from chainlog.sources import by_name


def plan(s, fork=False, here=False, rt=None):
    try:
        src = by_name(s["harness"])
    except KeyError:  # an event for a tool chainlog does not know (hand-edited or from a newer version)
        src = None
    argv = src.resume_argv(s["native_id"], fork=fork) if src else []  # a bug inside a real tool must surface, not hide
    cwd = s.get("cwd")
    notes, blockers = [], []
    if not argv:
        blockers.append("%r is not a tool chainlog can reopen." % s["harness"])
    if here or not cwd:
        cwd = (rt.env.get("PWD") if rt else None) or os.getcwd()
        if not s.get("cwd"):
            notes.append("this session's folder is unknown; resuming from the current folder (%s)." % cwd)
        elif s["harness"] == "claude" and os.path.realpath(cwd) != os.path.realpath(s["cwd"]):
            notes.append("Claude stores sessions per project folder; resuming from another folder may not find it.")
    elif not os.path.isdir(cwd):
        blockers.append("the session's folder no longer exists: %s (use --here to try from this folder)" % cwd)
    if not s["transcript"].get("present"):
        blockers.append("the transcript file is gone, so this session cannot be resumed (notes are kept).")
    if s["transcript"].get("archived") and s["harness"] == "codex":
        notes.append("this Codex session is archived; run: codex unarchive %s" % s["native_id"])
    if s.get("live") and not fork:
        notes.append("this session is still running%s; resuming opens a second copy. Use --fork for a "
                     "separate branch, or switch to it." % (" (pid %s)" % s["pid"] if s.get("pid") else ""))
    return {"cwd": cwd, "argv": argv, "notes": notes, "blockers": blockers}


def shell_command(p):
    if not p["argv"]:
        return ""
    return "cd %s && %s" % (shlex.quote(p["cwd"]), " ".join(shlex.quote(a) for a in p["argv"]))


def execute(p):
    os.chdir(p["cwd"])
    os.execvp(p["argv"][0], p["argv"])
