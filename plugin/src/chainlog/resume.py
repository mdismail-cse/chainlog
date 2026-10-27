"""Resume a session in its own folder with its own harness."""
from __future__ import annotations

import os
import re
import shlex
import sys

from chainlog import keep
from chainlog.sources import by_name

# Where a session was started. Claude Code records its entrypoint, Codex its source; both name the VS Code extension.
ORIGINS = {"claude-vscode": ("vscode", "VS Code"), "vscode": ("vscode", "VS Code"),
           "claude-desktop": ("desktop", "Desktop"), "cli": ("terminal", None)}
VSCODE_URI = "vscode://anthropic.claude-code/open?session=%s"  # the Claude Code extension's own "open this session" link
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def origin(s):
    """(key, label) of where the session was started: ("vscode", "VS Code"), ("desktop", "Desktop"),
    ("terminal", None), or (None, None) when unknown."""
    return ORIGINS.get(s.get("entrypoint")) or (None, None)


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
    restore = False
    if not s["transcript"].get("present"):
        if rt is not None and keep.has_copy(rt, s):
            restore = True  # Claude deleted the original; the copy chainlog kept is put back first
        else:
            blockers.append("the transcript file is gone, so this session cannot be resumed (notes are kept)."
                            + (" Next time: chainlog keep REF makes a copy Claude cannot delete." if s["harness"] == "claude" else ""))
    if s["transcript"].get("archived") and s["harness"] == "codex":
        notes.append("this Codex session is archived; run: codex unarchive %s" % s["native_id"])
    if s.get("live") and not fork:
        notes.append("this session is still running%s; resuming opens a second copy. Use --fork for a "
                     "separate branch, or switch to it." % (" (pid %s)" % s["pid"] if s.get("pid") else ""))
    return {"cwd": cwd, "argv": argv, "notes": notes, "blockers": blockers, "restore": restore, "ref": s["ref"]}


def vscode_command(s, p, platform=None):
    """A shell command that reopens a session started in the Claude Code VS Code extension in the extension's own panel,
    or "" when that is not possible. It opens the session's folder first (Claude looks sessions up per folder), then
    asks the extension for the session through its vscode:// link. `open` is used, so the caller needs no PATH."""
    if (platform or sys.platform) != "darwin" or s["harness"] != "claude" or origin(s)[0] != "vscode":
        return ""
    if p["blockers"] or not s.get("cwd") or not _UUID.match(s["native_id"]):
        return ""
    return _restoring(p, "open -a 'Visual Studio Code' %s && sleep 2 && open %s" % (
        shlex.quote(p["cwd"]), shlex.quote(VSCODE_URI % s["native_id"])))


def _restoring(p, command):
    """A session whose original Claude deleted is put back from the kept copy before anything opens it."""
    return "chainlog restore %s && %s" % (shlex.quote(p["ref"]), command) if p.get("restore") else command


def shell_command(p):
    if not p["argv"]:
        return ""
    return _restoring(p, "cd %s && %s" % (shlex.quote(p["cwd"]), " ".join(shlex.quote(a) for a in p["argv"])))


def execute(p):
    os.chdir(p["cwd"])
    os.execvp(p["argv"][0], p["argv"])
