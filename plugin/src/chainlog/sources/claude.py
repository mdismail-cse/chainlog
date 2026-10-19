"""Claude Code adapter (read-only).

Transcripts: ~/.claude/projects/<cwd-slug>/<sessionId>.jsonl (top level only; sub-agent
transcripts live in <project>/<id>/subagents/ and are never listed).
Live registry: ~/.claude/sessions/<pid>.json (the sibling *.key files are never opened).
"""
from __future__ import annotations

import json
import os
import re

from chainlog.paths import safe_open
from chainlog.redact import clean
from chainlog.sources.base import (file_state, iso_to_epoch, iter_json_lines, merge_prev,
                                   scan_markers)

REGISTRY_KEYS = ("sessionId", "pid", "procStart", "cwd", "name", "nameSource", "status", "version",
                 "kind", "entrypoint")
INTERACTIVE = ("cli", "claude-vscode", "claude-desktop")


def _user_text(o):
    if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain"):
        return None
    c = (o.get("message") or {}).get("content")
    if isinstance(c, str):
        t = c
    elif isinstance(c, list):
        t = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    else:
        return None
    t = t.strip()
    if not t or t.startswith("<") or t.startswith("[Request interrupted"):
        return None
    return t


def _assistant_text(o):
    if o.get("type") != "assistant" or o.get("isSidechain"):
        return None
    c = (o.get("message") or {}).get("content")
    if not isinstance(c, list):
        return None
    t = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text").strip()
    return t or None


class ClaudeSource:
    name = "claude"
    parser_version = 3

    def discover(self, rt):
        root = rt.claude_dir / "projects"
        try:
            projects = list(os.scandir(root))
        except OSError:
            return []
        out = []
        for p in projects:
            if not p.is_dir():
                continue
            try:
                for e in os.scandir(p.path):
                    if e.is_file() and e.name.endswith(".jsonl"):
                        out.append(e.path)
            except OSError:
                continue
        return out

    def parse(self, path, prev, rt, snippets=True):
        try:
            state = file_state(path, self.parser_version)
        except OSError:
            return None, None
        sid = os.path.basename(path)[: -len(".jsonl")]
        f = {"harness": "claude", "id": sid, "path": path, "titles": {}, "prs": [],
             "last_activity": state["mtime"], "child": False}
        last_ts = None
        try:
            for part, o in iter_json_lines(path, state["size"]):
                try:
                    t = o.get("type")
                    ts = o.get("timestamp")
                    if ts:
                        if part in ("head", "full") and not f.get("started_at"):
                            f["started_at"] = iso_to_epoch(ts)
                        last_ts = ts
                    if t in ("user", "assistant"):
                        if not f.get("cwd") and o.get("cwd"):
                            f["cwd"] = o["cwd"]
                        if not f.get("version") and o.get("version"):
                            f["version"] = o["version"]
                        if not f.get("entrypoint") and o.get("entrypoint"):
                            f["entrypoint"] = o["entrypoint"]
                        if o.get("gitBranch"):
                            f["branch"] = o["gitBranch"]
                        if t == "user":
                            u = _user_text(o)
                            if u:
                                f["has_prompt"] = True
                                if not f.get("first_ask"):
                                    f["first_ask"] = u
                                f["last_user"] = u
                        else:
                            a = _assistant_text(o)
                            if a:
                                f["last_work"] = a
                    elif t == "custom-title" and o.get("customTitle"):
                        f["titles"]["custom"] = o["customTitle"]
                    elif t == "agent-name" and o.get("agentName"):
                        f["titles"]["agent"] = o["agentName"]
                    elif t == "ai-title" and o.get("aiTitle"):
                        f["titles"]["ai"] = o["aiTitle"]
                    elif t == "last-prompt" and o.get("lastPrompt"):
                        f["last_prompt"] = o["lastPrompt"]
                    elif t == "pr-link" and str(o.get("prNumber") or "").isdigit():
                        pr = {"repo": o.get("prRepository") if isinstance(o.get("prRepository"), str) else None,
                              "number": int(o["prNumber"]),
                              "url": o.get("prUrl") if isinstance(o.get("prUrl"), str) else None, "source": "pr-link"}
                        if pr not in f["prs"]:
                            f["prs"].append(pr)
                    elif t == "cost-state":
                        f["stats"] = {"cost_usd": o.get("totalCostUSD"), "duration_ms": o.get("totalDuration"),
                                      "lines_added": o.get("totalLinesAdded"),
                                      "lines_removed": o.get("totalLinesRemoved")}
                    elif t == "bridge-session":
                        f["remote_control"] = True  # account / org ids on this line are never stored
                except (AttributeError, TypeError, ValueError, KeyError):
                    continue  # one odd line must never break indexing
            # a big file whose head+tail had no title: one marker-only full scan, then cached
            if state["size"] > 4 * 1024 * 1024 and not f["titles"] and not (prev or {}).get("titles"):
                for o in scan_markers(path, ['"custom-title"', '"ai-title"', '"agent-name"', '"pr-link"']):
                    t = o.get("type")
                    if t == "custom-title" and o.get("customTitle"):
                        f["titles"]["custom"] = o["customTitle"]
                    elif t == "ai-title" and o.get("aiTitle"):
                        f["titles"]["ai"] = o["aiTitle"]
                    elif t == "agent-name" and o.get("agentName"):
                        f["titles"]["agent"] = o["agentName"]
                    elif t == "pr-link" and o.get("prNumber"):
                        pr = {"repo": o.get("prRepository"), "number": o.get("prNumber"),
                              "url": o.get("prUrl"), "source": "pr-link"}
                        if pr not in f["prs"]:
                            f["prs"].append(pr)
        except OSError:
            f["warnings"] = ["unreadable"]
        if last_ts:
            f["last_ts"] = iso_to_epoch(last_ts)
        f = merge_prev(f, prev, ("cwd", "started_at", "version", "entrypoint", "first_ask", "has_prompt",
                                 "branch", "stats"))
        f["interactive"] = (f.get("entrypoint") or "cli") in INTERACTIVE
        ask = f.pop("last_prompt", None) or f.pop("last_user", None)
        f.pop("last_user", None)
        if snippets:
            f["last_ask"] = clean(ask, 160)
            f["last_work"] = clean(f.get("last_work"), 160)
            f["first_ask"] = clean(f.get("first_ask"), 120)
        else:
            f["last_ask"] = f["last_work"] = f["first_ask"] = ""
        return f, state

    def live(self, rt):
        """Live interactive sessions from the registry (allow-listed keys only)."""
        root = rt.claude_dir / "sessions"
        out = []
        try:
            names = [n for n in os.listdir(root) if n.endswith(".json")]
        except OSError:
            return out
        for n in names:
            try:
                with safe_open(root / n) as fh:
                    o = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(o, dict) or not o.get("sessionId") or not isinstance(o.get("pid"), int):
                continue
            o = {k: o.get(k) for k in REGISTRY_KEYS}
            if not rt.pid_alive(o["pid"]):
                continue
            if o.get("procStart") and not _same_proc_start(rt, o["pid"], o["procStart"]):
                continue  # pid reused by another process
            out.append({"ref": "claude:%s" % o["sessionId"], "pid": o["pid"], "cwd": o.get("cwd"),
                        "name": o.get("name"), "name_source": o.get("nameSource"), "status": o.get("status")})
        return out

    def id_from_env(self, env):
        v = env.get("CLAUDE_CODE_SESSION_ID") or ""
        return v if re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$", v) else None

    def resume_argv(self, native_id, fork=False):
        argv = ["claude", "--resume", native_id]
        if fork:
            argv.append("--fork-session")
        return argv


def _same_proc_start(rt, pid, proc_start):
    """PID-reuse guard. The registry records the start time in UTC while `ps -o lstart` prints
    local time, so the two may differ by a whole time-zone offset (multiple of 15 min, ≤14 h)."""
    import calendar
    import time as _time
    rc, out, _ = rt.run(["ps", "-o", "lstart=", "-p", str(pid)], timeout=2)
    if rc != 0 or not out.strip():
        return True  # cannot tell: trust the registry
    fmt = "%a %b %d %H:%M:%S %Y"
    try:
        a = calendar.timegm(_time.strptime(" ".join(out.split()), fmt))
        b = calendar.timegm(_time.strptime(" ".join(str(proc_start).split()), fmt))
    except ValueError:
        return True
    # the registry is UTC; ps prints local time: they differ by exactly this machine's offset
    # at that moment (DST-aware), or not at all if the registry ever switches to local time
    offset = _time.localtime(b).tm_gmtoff or 0
    return (a - b) in (0, offset)


def retention_days(rt):
    """Claude Code deletes transcripts this many days after their last activity
    (settings.json "cleanupPeriodDays", default 30). Only that one key is read."""
    try:
        with safe_open(rt.claude_dir / "settings.json") as fh:
            v = json.load(fh).get("cleanupPeriodDays")
        return int(v) if isinstance(v, (int, float)) and v > 0 else 30
    except (OSError, ValueError, AttributeError):
        return 30
