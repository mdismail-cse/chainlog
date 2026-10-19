"""Setup components: cli (~/.local/bin link + stable app copy), claude (plugin via the claude CLI),
codex (user hooks.json SessionEnd entry + skills), shell (zsh prompt hook), scheduler (launchd /
systemd timer). Every plan is idempotent: a part that is already in place plans nothing.

Never edited: herdr's hook scripts, herdr's hook entries, ~/.claude/settings.json.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

from chainlog.integrations.base import Change, read_text
from chainlog.paths import safe_open

MARK_BEGIN = "# >>> chainlog >>>"
MARK_END = "# <<< chainlog <<<"
HOOK_MARK = "spool.sh"  # with a command ending in "codex end": see is_ours()
PLIST_LABEL = "com.chainlog.sweep"


def plugin_root():
    """The plugin/ directory this code runs from (…/plugin/src/chainlog -> …/plugin)."""
    return Path(__file__).resolve().parents[3]


def repo_root():
    return plugin_root().parent


def launcher_path():
    return plugin_root() / "bin" / "chainlog"


def version():
    try:
        return (plugin_root() / "VERSION").read_text().strip()
    except OSError:
        return "0"


def _default_home(rt):
    base = rt.env.get("XDG_DATA_HOME") or str(rt.home / ".local" / "share")
    return Path(base) / "chainlog"


def _custom_home(rt):
    return os.path.abspath(str(rt.data_home)) != os.path.abspath(str(_default_home(rt)))


def _read_json(path, rt):
    try:
        with safe_open(path, rt=rt) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# --- claude ------------------------------------------------------------------------------------
def plan_claude(rt, source=None):
    """Install the plugin through Claude's own CLI. source: GitHub slug (default) or a local path."""
    if not shutil.which("claude", path=rt.env.get("PATH")):
        return []
    src = source or "mdismail-cse/chainlog"
    known = _read_json(rt.claude_dir / "plugins" / "known_marketplaces.json", rt) or {}
    installed = read_text(rt.claude_dir / "plugins" / "installed_plugins.json", rt) or ""
    changes = []
    if "chainlog" not in known:
        changes.append(Change("claude", "add the chainlog plugin marketplace (%s)" % src,
                              command=["claude", "plugin", "marketplace", "add", src],
                              undo_command=["claude", "plugin", "marketplace", "remove", "chainlog"]))
    if '"chainlog@' not in installed:
        changes.append(Change("claude", "install the chainlog plugin for your user",
                              command=["claude", "plugin", "install", "chainlog@chainlog", "--scope", "user"],
                              undo_command=["claude", "plugin", "uninstall", "chainlog@chainlog"]))
    return changes


# --- codex -------------------------------------------------------------------------------------
def stable_hook(rt):
    return rt.data_home / "hook" / "spool.sh"


def is_ours(group):
    for h in (group.get("hooks") or []) if isinstance(group, dict) else []:
        cmd = h.get("command") if isinstance(h, dict) else None
        if isinstance(cmd, str) and HOOK_MARK in cmd and cmd.rstrip().endswith("codex end"):
            return True
    return False


def hook_command(rt):
    return "/bin/sh %s codex end" % shlex.quote(str(stable_hook(rt)))


def _stub_text(rt):
    """The stub with this data dir baked in: Codex may not pass CHAINLOG_HOME to its hooks."""
    text = (plugin_root() / "hooks" / "spool.sh").read_text()
    baked = 'd=%s' % shlex.quote(str(rt.data_home / "spool"))
    return re.sub(r'^d="\$\{CHAINLOG_HOME:-.*$', baked + "   # baked by chainlog setup", text, count=1, flags=re.M)


def _json_style(text):
    """(indent, trailing_newline) of an existing JSON file, so a rewrite changes only our entry."""
    if not text or not text.strip():
        return 2, True
    if "\n" not in text.strip():
        return None, text.endswith("\n")
    m = re.search(r"\n([ \t]+)\S", text)
    indent = (m.group(1) if m.group(1).startswith("\t") else len(m.group(1))) if m else 2
    return indent, text.endswith("\n")


def _dump_like(data, before):
    indent, nl = _json_style(before)
    if indent is None:
        out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        out = json.dumps(data, ensure_ascii=False, indent=indent)
    return out + ("\n" if nl else "")


def _skip_string(text, i):
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    raise ValueError("unterminated string")


def _hooks_span(text):
    """(start, end) of the top-level "hooks" object's braces in a JSON text, or None."""
    depth, j = 0, 0
    while j < len(text):
        c = text[j]
        if c == '"':
            end = _skip_string(text, j)
            if depth == 1:
                k = end
                while k < len(text) and text[k] in " \t\r\n":
                    k += 1
                if k < len(text) and text[k] == ":" and json.loads(text[j:end]) == "hooks":
                    k += 1
                    while k < len(text) and text[k] in " \t\r\n":
                        k += 1
                    if k < len(text) and text[k] == "{":
                        d, m = 0, k
                        while m < len(text):
                            ch = text[m]
                            if ch == '"':
                                m = _skip_string(text, m)
                                continue
                            if ch in "{[":
                                d += 1
                            elif ch in "}]":
                                d -= 1
                                if d == 0:
                                    return k, m
                            m += 1
                    return None
            j = end
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        j += 1
    return None


def _insert_session_end(before, entry):
    """Add a "SessionEnd" key to the hooks object by inserting text, so every byte of the existing
    entries (herdr's) stays exactly as it was. None when the file's shape needs a full rewrite."""
    try:
        data = json.loads(before)
        span = _hooks_span(before)
    except ValueError:
        return None
    if span is None or "SessionEnd" in data.get("hooks", {}) or not data["hooks"]:
        return None
    start, end = span
    q = end - 1
    while q > start and before[q] in " \t\r\n":
        q -= 1
    indent, _nl = _json_style(before)
    if indent is None:
        frag = ',"SessionEnd":' + json.dumps([entry], ensure_ascii=False, separators=(",", ":"))
    else:
        unit = indent if isinstance(indent, str) else " " * indent
        eol = "\r\n" if "\r\n" in before else "\n"
        body = json.dumps([entry], ensure_ascii=False, indent=indent).split("\n")
        frag = "," + eol + unit * 2 + '"SessionEnd": ' + (eol + unit * 2).join(body)
    after = before[:q + 1] + frag + before[q + 1:]
    expected = dict(data)
    expected["hooks"] = dict(data["hooks"], SessionEnd=[entry])
    try:
        return after if json.loads(after) == expected else None
    except ValueError:
        return None


def plan_codex(rt):
    if not (shutil.which("codex", path=rt.env.get("PATH")) or rt.codex_dir.is_dir()):
        return []
    changes = []
    # 1. chainlog's own stable copy of the stub: the hooks.json command string never changes across
    #    upgrades, so Codex's trust approval survives them
    hook = stable_hook(rt)
    changes.append(Change("codex", "chainlog's own copy of the hook stub", path=hook,
                          before=read_text(hook, rt), after=_stub_text(rt), mode=0o700))
    # 2. one SessionEnd entry in ~/.codex/hooks.json (herdr's entries untouched, file style kept)
    hooks_json = rt.codex_dir / "hooks.json"
    before = read_text(hooks_json, rt)
    try:
        data = json.loads(before) if before and before.strip() else {}
    except ValueError:
        raise RuntimeError("%s is not valid JSON; chainlog will not edit it. Fix it first." % hooks_json)
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise RuntimeError("%s has an unexpected shape; chainlog will not edit it." % hooks_json)
    existing = data.get("hooks", {}).get("SessionEnd", [])
    if not isinstance(existing, list):
        raise RuntimeError("%s: hooks.SessionEnd is not a list; chainlog will not edit it." % hooks_json)
    entry = {"hooks": [{"type": "command", "command": hook_command(rt), "timeout": 2}]}
    if [g for g in existing if is_ours(g)] == [entry]:
        after = before
    else:
        after = _insert_session_end(before, entry) if (before and not existing) else None
        if after is None:
            data.setdefault("hooks", {})["SessionEnd"] = [g for g in existing if not is_ours(g)] + [entry]
            after = _dump_like(data, before)
    if before is not None and os.path.exists(hooks_json) and not os.access(hooks_json, os.W_OK):
        raise RuntimeError("%s is read-only; chainlog will not replace it." % hooks_json)
    changes.append(Change("codex", "add a SessionEnd hook to ~/.codex/hooks.json (needs /hooks trust in codex)",
                          path=hooks_json, before=before, after=after, harness=True))
    # 3. skills
    for name in ("chainlog-save", "chainlog-find"):
        src = plugin_root() / "codex" / "skills" / name / "SKILL.md"
        dst = rt.codex_dir / "skills" / name / "SKILL.md"
        changes.append(Change("codex", "Codex skill %s" % name, path=dst, before=read_text(dst, rt),
                              after=src.read_text(), harness=True))
    return changes


def our_session_end_index(text):
    """Position of our group in hooks.SessionEnd (Codex keys trust by position), or None."""
    try:
        groups = json.loads(text).get("hooks", {}).get("SessionEnd", [])
    except (ValueError, AttributeError, TypeError):
        return None
    for i, g in enumerate(groups if isinstance(groups, list) else []):
        if is_ours(g):
            return i
    return None


def remove_codex_entry(text):
    """Remove only our SessionEnd entry (used when hooks.json changed after install)."""
    data = json.loads(text)
    hooks = data.get("hooks", {})
    groups = [g for g in hooks.get("SessionEnd", []) if not is_ours(g)]
    if groups:
        hooks["SessionEnd"] = groups
    else:
        hooks.pop("SessionEnd", None)
    return _dump_like(data, text)


# --- shell -------------------------------------------------------------------------------------
def stable_shell(rt):
    return rt.data_home / "shell" / "chainlog.zsh"


def zshrc(rt):
    return Path(rt.env.get("ZDOTDIR") or rt.home) / ".zshrc"


def _bin_on_path(rt):
    b = os.path.abspath(str(rt.home / ".local" / "bin"))
    return any(os.path.abspath(p) == b for p in (rt.env.get("PATH") or "").split(":") if p)


def plan_shell(rt):
    changes = []
    src = (plugin_root() / "shell" / "chainlog.zsh").read_text()
    dst = stable_shell(rt)
    changes.append(Change("shell", "chainlog's own copy of the zsh prompt hook", path=dst,
                          before=read_text(dst, rt), after=src))
    rc = zshrc(rt)
    before = read_text(rc, rt)
    lines = [MARK_BEGIN]
    if _custom_home(rt):
        lines.append("export CHAINLOG_HOME=%s" % shlex.quote(str(rt.data_home)))
    if not _bin_on_path(rt):
        lines.append('[[ ":$PATH:" == *":$HOME/.local/bin:"* ]] || export PATH="$HOME/.local/bin:$PATH"')
    q = shlex.quote(str(dst))
    lines += ["[[ -r %s ]] && source %s" % (q, q), MARK_END]
    block = "\n".join(lines) + "\n"
    text = before or ""
    if MARK_BEGIN in text:
        after = text
    else:
        after = text + ("" if not text or text.endswith("\n") else "\n") + block
    changes.append(Change("shell", "source the prompt hook from ~/.zshrc (one marked block)", path=rc,
                          before=before, after=after))
    return changes


def remove_shell_block(text):
    """Remove our marked block. If the end marker was deleted by hand, remove only the begin marker
    and our own lines right after it — never the rest of the file."""
    lines = text.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == MARK_BEGIN:
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == MARK_END), None)
            if end is not None:
                i = end + 1
                continue
            i += 1
            while i < len(lines) and ("chainlog" in lines[i] or ".local/bin" in lines[i]) \
                    and lines[i].strip() != MARK_BEGIN:
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


# --- scheduler ---------------------------------------------------------------------------------
def plist_path(rt):
    return rt.home / "Library" / "LaunchAgents" / ("%s.plist" % PLIST_LABEL)


def systemd_dir(rt):
    return Path(rt.env.get("XDG_CONFIG_HOME") or rt.home / ".config") / "systemd" / "user"


def _plist(rt, launcher):
    path_env = rt.env.get("PATH", "/usr/bin:/bin")
    log = rt.data_home / "sweep.log"
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>%s</string><string>_sweep</string><string>--cron</string></array>
  <key>StartInterval</key><integer>120</integer>
  <key>RunAtLoad</key><false/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>%s</string>
    <key>HOME</key><string>%s</string>
    <key>CHAINLOG_HOME</key><string>%s</string>
  </dict>
  <key>StandardOutPath</key><string>%s</string>
  <key>StandardErrorPath</key><string>%s</string>
</dict>
</plist>
""" % (PLIST_LABEL, esc(launcher), esc(path_env), esc(rt.home), esc(rt.data_home), esc(log), esc(log))


def _stable_launcher(rt):
    for cand in (rt.data_home / "app" / "current" / "plugin" / "bin" / "chainlog",
                 rt.home / ".local" / "bin" / "chainlog"):
        if os.path.exists(cand):
            return cand
    return launcher_path()


def plan_scheduler(rt):
    launcher = _stable_launcher(rt)
    if sys.platform == "darwin":
        p = plist_path(rt)
        uid = os.getuid()
        before, after = read_text(p, rt), _plist(rt, launcher)
        loaded = scheduler_loaded(rt)
        changes = [Change("scheduler", "launchd agent: run the unsaved-session sweep every 2 min", path=p,
                          before=before, after=after)]
        if loaded and before != after:
            changes.append(Change("scheduler", "unload the old launchd agent",
                                  command=["launchctl", "bootout", "gui/%d/%s" % (uid, PLIST_LABEL)]))
        if not loaded or before != after:
            changes.append(Change("scheduler", "load the launchd agent",
                                  command=["launchctl", "bootstrap", "gui/%d" % uid, str(p)],
                                  undo_command=["launchctl", "bootout", "gui/%d/%s" % (uid, PLIST_LABEL)]))
        return changes
    if shutil.which("systemctl", path=rt.env.get("PATH")):
        d = systemd_dir(rt)
        svc = ("[Unit]\nDescription=chainlog unsaved-session sweep\n\n[Service]\nType=oneshot\n"
               "ExecStart=/bin/sh %s _sweep --cron\n" % shlex.quote(str(launcher)))
        tmr = ("[Unit]\nDescription=chainlog unsaved-session sweep timer\n\n[Timer]\nOnBootSec=2min\n"
               "OnUnitActiveSec=2min\n\n[Install]\nWantedBy=timers.target\n")
        changes = [
            Change("scheduler", "systemd user service", path=d / "chainlog-sweep.service",
                   before=read_text(d / "chainlog-sweep.service", rt), after=svc),
            Change("scheduler", "systemd user timer (every 2 min)", path=d / "chainlog-sweep.timer",
                   before=read_text(d / "chainlog-sweep.timer", rt), after=tmr),
        ]
        if not scheduler_loaded(rt) or not all(c.noop for c in changes):
            changes.append(Change("scheduler", "enable the timer",
                                  command=["systemctl", "--user", "enable", "--now", "chainlog-sweep.timer"],
                                  undo_command=["systemctl", "--user", "disable", "--now", "chainlog-sweep.timer"]))
        return changes
    return []


def scheduler_loaded(rt):
    if sys.platform == "darwin":
        rc, _, _ = rt.run(["launchctl", "print", "gui/%d/%s" % (os.getuid(), PLIST_LABEL)], timeout=3)
        return rc == 0
    rc, _, _ = rt.run(["systemctl", "--user", "is-active", "chainlog-sweep.timer"], timeout=3)
    return rc == 0


# --- cli link ----------------------------------------------------------------------------------
def plan_cli(rt):
    """`chainlog` on PATH. An existing working link or file is left alone (install.sh's link, or the
    user's own). Otherwise link to the installed app copy; when there is none (running from a
    checkout or from the Claude plugin cache, whose path changes on every plugin update), first
    install a stable app copy of this version."""
    link = rt.home / ".local" / "bin" / "chainlog"
    if os.path.lexists(link) and os.path.exists(os.path.realpath(link)):
        return []
    current = rt.data_home / "app" / "current"
    target = current / "plugin" / "bin" / "chainlog"
    if target.exists():
        return [Change("cli", "link ~/.local/bin/chainlog -> the installed app",
                       display="ln -sfn %s ~/.local/bin/chainlog" % target,
                       command=["sh", "-c", 'mkdir -p "$1" && ln -sfn "$2" "$1/chainlog"', "sh",
                                str(link.parent), str(target)],
                       undo_command=["rm", "-f", str(link)])]
    dest = rt.data_home / "app" / ("v" + version())
    script = ('set -e; rm -rf "$2"; mkdir -p "$2"; cp -R "$1" "$2/plugin"; ln -sfn "$2" "$3"; '
              'mkdir -p "$4"; ln -sfn "$3/plugin/bin/chainlog" "$4/chainlog"')
    return [Change("cli", "install the CLI into %s and link ~/.local/bin/chainlog" % dest,
                   display="cp -R %s %s/plugin; ln -sfn …/app/current/plugin/bin/chainlog ~/.local/bin/chainlog"
                   % (plugin_root(), dest),
                   command=["sh", "-c", script, "sh", str(plugin_root()), str(dest), str(current), str(link.parent)],
                   undo_command=["rm", "-f", str(link)])]
