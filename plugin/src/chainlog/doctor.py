"""`chainlog doctor`: read-only health checks (schema chainlog.doctor/1)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys

from chainlog import __version__
from chainlog import config as config_mod
from chainlog.integrations import components as comp
from chainlog.paths import safe_open

def herdr_files(rt):
    return {"claude": rt.claude_dir / "hooks" / "herdr-agent-state.sh",
            "codex": rt.codex_dir / "herdr-agent-state.sh"}


def herdr_hashes(rt):
    """sha256 of herdr's managed hook scripts (read-only; chainlog never writes them)."""
    out = {}
    for key, path in herdr_files(rt).items():
        try:
            with safe_open(path, "rb") as f:
                out[key] = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            out[key] = None
    return out


def codex_hook_trusted(rt):
    """Codex keys hook trust by file and position: '.../hooks.json:session_end:<group>:<hook>'.
    A hook that has already fired counts as approved too."""
    try:
        with safe_open(rt.data_home / "events.jsonl") as f:
            if '"src":"hook:codex"' in f.read():
                return True
    except OSError:
        pass
    try:
        with safe_open(rt.codex_dir / "hooks.json") as f:
            idx = comp.our_session_end_index(f.read())
        if idx is None:
            return False
        with safe_open(rt.codex_dir / "config.toml") as f:
            return ("hooks.json:session_end:%d:0" % idx) in f.read()
    except OSError:
        return False


def _check(cid, ok, detail, fix=None, severity="error"):
    return {"id": cid, "ok": bool(ok), "severity": "ok" if ok else severity, "detail": detail, "fix": fix}


def run(rt, index, cfg):
    checks = []
    v = sys.version_info
    checks.append(_check("python3", v >= (3, 9), "Python %d.%d.%d" % v[:3],
                         "install Python 3.9+ (xcode-select --install or brew install python)"))
    on_path = shutil.which("chainlog", path=rt.env.get("PATH"))
    link = rt.home / ".local" / "bin" / "chainlog"
    fix = "chainlog setup cli   (or run install.sh)"
    if not on_path and os.path.exists(link):
        fix = 'add to ~/.zshrc:  export PATH="$HOME/.local/bin:$PATH"   (then open a new terminal)'
    checks.append(_check("cli_on_path", on_path, "ready" if on_path else "not available in your Terminal yet", fix, "warn"))
    app = rt.data_home / "app" / "current" / "plugin" / "VERSION"
    try:
        app_v = app.read_text().strip()
    except OSError:
        app_v = None
    checks.append(_check("version_skew", app_v in (None, __version__),
                         "%s%s" % (__version__, " (installed copy: %s)" % app_v if app_v and app_v != __version__ else ""),
                         "re-run install.sh or update the plugin", "warn"))
    has_claude = bool(shutil.which("claude", path=rt.env.get("PATH"))) or (rt.claude_dir / "projects").is_dir()
    installed = rt.claude_dir / "plugins" / "installed_plugins.json"
    try:
        with safe_open(installed) as f:
            has_plugin = "chainlog@" in f.read()
    except OSError:
        has_plugin = False
    if has_claude:
        checks.append(_check("claude_plugin", has_plugin,
                             "connected" if has_plugin else "not connected: chainlog can't tell exactly which session you left",
                             "chainlog setup claude", "warn"))
    hooks_json = rt.codex_dir / "hooks.json"
    try:
        with safe_open(hooks_json) as f:
            hj = f.read()
    except OSError:
        hj = ""
    present = comp.our_session_end_index(hj) is not None
    if shutil.which("codex") or rt.codex_dir.is_dir():
        checks.append(_check("codex_hook_present", present,
                             "connected" if present else
                             "not connected (sessions are still listed and searchable)",
                             "chainlog setup codex", "warn"))
        trusted = False
        idx = comp.our_session_end_index(hj) if present else None
        if idx is not None:
            try:
                with safe_open(rt.codex_dir / "config.toml") as f:
                    # Codex keys hook trust by file and position: ".../hooks.json:session_end:<group>:<hook>"
                    trusted = ("hooks.json:session_end:%d:0" % idx) in f.read()
            except OSError:
                pass
        fired = any(e.get("type") == "hook.end" and e.get("src") == "hook:codex" for e in index.events)
        checks.append(_check("codex_hooks_trusted", trusted or fired or not present,
                             "not needed yet" if not present else
                             ("approved" if trusted else ("working" if fired else "waiting for your approval")),
                             "open codex, type /hooks, and approve the chainlog entry", "warn"))
        stale = []
        for name in ("chainlog-save", "chainlog-find"):
            mine = comp.plugin_root() / "codex" / "skills" / name / "SKILL.md"
            theirs = rt.codex_dir / "skills" / name / "SKILL.md"
            try:
                if theirs.read_text() != mine.read_text():
                    stale.append(name + " (outdated)")
            except OSError:
                stale.append(name + " (missing)")
        checks.append(_check("codex_skill", not stale, "Codex skills up to date" if not stale else ", ".join(stale),
                             "chainlog setup codex", "warn"))
        if present:
            try:
                ok_stub = comp.stable_hook(rt).read_text() == comp._stub_text(rt)
            except OSError:
                ok_stub = False
            checks.append(_check("codex_hook_stub", ok_stub, "up to date" if ok_stub else
                                 "missing or from an older version", "chainlog setup codex"))
    recorded = (cfg.get("herdr_hashes") or {})
    now_h = herdr_hashes(rt)
    if any(now_h.values()):
        same = all(recorded.get(k) in (None, v) for k, v in now_h.items())
        checks.append(_check("herdr_untouched", same,
                             "herdr hook scripts unchanged since setup" if same else
                             "herdr hook scripts changed since setup (herdr updated itself, or something edited them)",
                             "nothing to do if you updated herdr; chainlog never edits these files", "warn"))
    perms_ok = True
    try:
        mode = stat.S_IMODE(os.stat(rt.data_home).st_mode)
        perms_ok = mode & 0o077 == 0
    except OSError:
        pass
    checks.append(_check("store_perms", perms_ok, "%s is private" % rt.data_home if perms_ok else
                         "%s is readable by others" % rt.data_home, "chmod 700 %s" % rt.data_home))
    from chainlog.sources.claude import retention_days
    days = retention_days(rt) if has_claude else 365
    saved_claude = [x for x in index.sessions.values() if x["harness"] == "claude" and x["saved"] != "none"]
    soon = [x for x in saved_claude if x["transcript"].get("present")
            and (rt.now() - (x["times"].get("last_activity_at") or rt.now())) > (days - 7) * 86400]
    if has_claude:
        checks.append(_check("claude_retention", days >= 60 and not soon,
                             "Claude Code deletes sessions %d days after last use, so older ones can't be reopened%s" % (
                                 days, "; %d saved session%s go within a week" % (len(soon), "" if len(soon) == 1 else "s")
                                 if soon else " (optional)"),
                             'to keep them longer, add "cleanupPeriodDays": 90 to ~/.claude/settings.json', "info"))
    from chainlog.store.events import torn_lines
    torn = torn_lines(rt)
    checks.append(_check("event_log", torn == 0, "ok" if not torn else
                         "%d unreadable line%s in events.jsonl (a crash mid-write; later saves are safe)"
                         % (torn, "" if torn == 1 else "s"), None, "warn"))
    backlog = 0
    try:
        backlog = len([n for n in os.listdir(rt.data_home / "spool") if n.endswith(".json")])
    except OSError:
        pass
    checks.append(_check("spool_backlog", backlog < 50, "none waiting" if not backlog else "%d waiting" % backlog,
                         "run any chainlog command with write access", "warn"))
    vis = index.visible()
    counts = {}
    for s in vis:
        counts[s["harness"]] = counts.get(s["harness"], 0) + 1
    warn = sum(1 for s in vis if s.get("warnings"))
    checks.append(_check("parse_rate", warn == 0, "%s%s" % (
        ", ".join("%d %s" % (n, h.capitalize()) for h, n in sorted(counts.items())) or "none yet",
        ", %d with warnings" % warn if warn else ""), None, "warn"))
    try:
        with safe_open(comp.zshrc(rt)) as f:
            shell = comp.MARK_BEGIN in f.read()
    except OSError:
        shell = False
    checks.append(_check("shell_hint", shell, "on" if shell else "off (optional)",
                         "chainlog setup shell", "info"))
    action = cfg["unsaved"]["action"]
    engine = cfg["summary"]["engine"]
    needs = action in ("summarize", "remind")
    loaded = comp.scheduler_loaded(rt) if needs else True
    if needs and sys.platform == "darwin" and not comp.plist_path(rt).exists():
        loaded = False
    plain = {"summarize": "auto-save with a summary", "list": "keep a list", "remind": "remind you", "off": "nothing"}
    checks.append(_check("unsaved_policy", loaded, "%s, %s after a session ends%s" % (
        plain.get(action, action), config_mod.fmt_duration(cfg["unsaved"]["after"]),
        "" if not needs else ("" if loaded else " — but the background timer is off")),
        "chainlog setup policy", "warn"))
    if action == "summarize" and engine != "local":
        bins = ["claude", "codex"] if engine == "auto" else [engine]
        have = [b for b in bins if shutil.which(b, path=rt.env.get("PATH"))]
        checks.append(_check("summarizer", bool(have), "engine %s: %s" % (engine, ", ".join(have) or "no binary"),
                             "install the harness CLI or: chainlog config set summary.engine local", "warn"))
    ok = all(c["ok"] or c["severity"] in ("warn", "info") for c in checks)
    return {"schema": "chainlog.doctor/1", "ok": ok, "version": __version__, "checks": checks}


LABELS = {
    "python3": "Python", "cli_on_path": "chainlog command", "version_skew": "version",
    "claude_plugin": "Claude Code", "codex_hook_present": "Codex", "codex_hooks_trusted": "Codex approval",
    "codex_skill": "Codex skills", "codex_hook_stub": "Codex connection file", "herdr_untouched": "herdr files",
    "store_perms": "private notes folder", "claude_retention": "old sessions", "event_log": "notes file",
    "spool_backlog": "pending updates", "parse_rate": "sessions found", "shell_hint": "Terminal reminder",
    "unsaved_policy": "if you forget to save", "summarizer": "summaries",
}


def render(report, st):
    problems = [c for c in report["checks"] if not c["ok"] and c["severity"] == "error"]
    tips = [c for c in report["checks"] if not c["ok"] and c["severity"] != "error"]
    if problems:
        head = st.red("✗ %d problem%s to fix" % (len(problems), "" if len(problems) == 1 else "s"))
    elif tips:
        head = st.green("✓ chainlog works.") + " %d suggestion%s below (marked !), all optional." % (
            len(tips), "" if len(tips) == 1 else "s")
    else:
        head = st.green("✓ Everything is set up.")
    lines = [head, ""]
    for c in report["checks"]:
        mark = st.green("✓") if c["ok"] else (st.yellow("!") if c["severity"] in ("warn", "info") else st.red("✗"))
        lines.append(" %s %-22s %s" % (mark, LABELS.get(c["id"], c["id"]), c["detail"]))
        if not c["ok"] and c.get("fix"):
            lines.append("   %s %s" % (" " * 22, st.dim("→ " + c["fix"])))
    return "\n".join(lines)


def to_json(report):
    return json.dumps(report, indent=2)
