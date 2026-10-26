"""`chainlog setup` / `chainlog uninstall`.

setup: per component, show what will change (a unified diff for edits, the content of new files),
ask, then apply with backup + re-check. `policy` asks the unsaved-session question (plan §3.7).
Without a terminal and without --yes nothing is applied: the plan is shown and setup exits 2.
uninstall: walk the manifest backwards; a file still exactly as we left it is restored from its
backup byte-for-byte; a file someone changed since only loses our marked entry.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from chainlog import config as config_mod
from chainlog.integrations import base
from chainlog.integrations import components as comp
from chainlog.paths import atomic_write

ORDER = ("cli", "claude", "codex", "shell", "policy", "scheduler", "menubar")
OPT_IN = ("scheduler", "menubar")  # only when asked for by name (or pulled in by the policy), never by "all"

# what each part is, in plain words (interactive setup asks these instead of showing raw files)
QUESTIONS = {
    "cli": "Add the `chainlog` command to your Terminal?",
    "claude": "Connect chainlog to Claude Code? (saves which session you leave, adds /chainlog:save)",
    "codex": "Connect chainlog to Codex? (saves which session you leave, adds a 'save to chainlog' skill)",
    "shell": "Show a one-line note in Terminal right after you leave a session without saving?",
    "scheduler": "Turn on the small background timer? (needed for reminders and auto-save; checks every 2 min)",
    "menubar": "Add the Mac menu bar app? (an icon at the top of the screen: click it to see your sessions and reopen one)",
}

POLICY_Q = """When a session ends and you haven't saved it within {after}, chainlog should:
  1) Auto-save it with a short summary of the session    (nothing is ever lost)
  2) Keep it in your "unsaved" list with a preview of the last work   (you decide later)
  3) Remind you with a pop-up notification
  4) Do nothing"""
ENGINE_Q = """Who writes the summary?
  a) chainlog, from the session's title and last messages   (instant, free, works offline)
  b) the AI that ran the session   (a better summary; uses a little of your Claude/Codex usage)"""


class Aborted(Exception):
    pass


def _ask(prompt, default=None):
    try:
        ans = input(prompt).strip()
    except EOFError:
        print("")
        return None
    except KeyboardInterrupt:
        print("")
        raise Aborted()
    return ans or default


def _plan(rt, name, args):
    if name == "cli":
        return comp.plan_cli(rt)
    if name == "claude":
        return comp.plan_claude(rt, source=str(comp.repo_root()) if args.local else None)
    if name == "codex":
        return comp.plan_codex(rt)
    if name == "shell":
        return comp.plan_shell(rt)
    if name == "scheduler":
        return comp.plan_scheduler(rt)
    if name == "menubar":
        return comp.plan_menubar(rt)
    return []


def _render(rt, ch, out):
    if ch.noop:
        out("  = %s (already done)" % ch.description)
        return
    out("  + %s" % ch.description)
    if ch.command:
        out("      " + ch.diff(rt))
    elif ch.before is None:
        lines = (ch.after or "").splitlines()
        out("      create %s:" % _home(rt, ch.path))
        for line in lines[:40]:
            out("      | " + line)
        if len(lines) > 40:
            out("      | … (%d more lines)" % (len(lines) - 40))
    else:
        for line in ch.diff(rt).splitlines():
            out("      " + line)


def _home(rt, p):
    s, h = str(p), str(rt.home)
    return "~" + s[len(h):] if s.startswith(h) else s


def _policy(rt, args, cfg, out, interactive):
    after = config_mod.fmt_duration(cfg["unsaved"]["after"])
    action, engine = args.policy, args.engine
    if action is None:
        if not interactive:
            action = cfg["unsaved"]["action"]  # agents pass --policy after asking the user
        else:
            out(POLICY_Q.format(after=after))
            ans = _ask("Choose [1-4] (Enter = keep %s): " % cfg["unsaved"]["action"], "")
            if ans is None:
                return cfg, False
            action = {"1": "summarize", "2": "list", "3": "remind", "4": "off"}.get(ans, cfg["unsaved"]["action"])
    if action == "summarize" and engine is None:
        if not interactive:
            engine = cfg["summary"]["engine"]
        else:
            out(ENGINE_Q)
            ans = _ask("Choose [a-b] (Enter = a): ", "a")
            engine = "auto" if (ans or "a").lower().startswith("b") else "local"
    cfg = config_mod.set_value(cfg, "unsaved.action", action)
    if engine:
        cfg = config_mod.set_value(cfg, "summary.engine", engine)
    plain = {"summarize": "auto-save with a summary", "list": "keep them in the Unsaved list",
             "remind": "remind you", "off": "do nothing"}[action]
    out("✓ Forgotten sessions: %s (%s after the session ends).   [unsaved.action = %s]" % (plain, after, action))
    return cfg, action in ("summarize", "remind")


def _present(rt, name):
    if name == "claude":
        return bool(shutil.which("claude", path=rt.env.get("PATH")))
    if name == "codex":
        return bool(shutil.which("codex", path=rt.env.get("PATH"))) or rt.codex_dir.is_dir()
    if name == "codex_hook":
        text = base.read_text(rt.codex_dir / "hooks.json", rt) or ""
        return comp.our_session_end_index(text) is not None
    if name == "scheduler":
        return sys.platform == "darwin" or bool(shutil.which("systemctl", path=rt.env.get("PATH")))
    if name == "menubar":
        return comp.menubar_ready(rt)
    return True


def run(rt, args, out):
    try:
        return _run(rt, args, out)
    except Aborted:
        out("setup cancelled; nothing more was changed")
        return 130


def _run(rt, args, out):
    from chainlog.doctor import herdr_hashes
    say = (lambda *a: sys.stderr.write(" ".join(str(x) for x in a) + "\n")) if args.json else out
    unknown = [c for c in list(args.components or []) + list(args.skip or []) if c not in ORDER + ("all",)]
    if unknown:
        msg = "unknown part%s: %s (known: all, %s)" % ("s" if len(unknown) > 1 else "", ", ".join(unknown), ", ".join(ORDER))
        if args.json:
            out(json.dumps({"schema": "chainlog.setup/1", "applied": False, "ok": False, "error": msg}, indent=2))
        else:
            say(msg)
        return 2
    cfg = config_mod.load(rt)
    if not cfg.get("installed_at"):
        cfg["installed_at"] = int(rt.now())
    cfg["herdr_hashes"] = cfg.get("herdr_hashes") or herdr_hashes(rt)
    wanted = list(args.components or ["all"])
    if "all" in wanted:
        wanted = [c for c in ORDER if c not in OPT_IN]
    wanted = [c for c in ORDER if c in wanted and c not in args.skip]
    report = []
    interactive = rt.isatty() and not args.yes and not args.json
    no_consent = not rt.isatty() and not args.yes and not args.dry_run
    if no_consent:
        # nobody to ask (an agent, a pipe): never apply silently — show the plan and stop
        args.dry_run = True
    i = 0
    while i < len(wanted):
        name = wanted[i]
        i += 1
        if name == "policy":
            action = args.policy or cfg["unsaved"]["action"]
            if args.dry_run:
                say("\n[policy]")
                if args.policy:
                    say("  + set unsaved.action = %s%s" % (args.policy, (" · summary.engine = %s" % args.engine)
                                                           if args.engine else ""))
                else:
                    say("  ? would ask what to do with sessions left unsaved for %s (now: %s)"
                        % (config_mod.fmt_duration(cfg["unsaved"]["after"]), cfg["unsaved"]["action"]))
                needs_timer = action in ("summarize", "remind")
            else:
                say("\nWhat should happen when you forget to save a session?" if interactive else "\n[policy]")
                cfg, needs_timer = _policy(rt, args, cfg, say, interactive)
                config_mod.save(rt, cfg)
                report.append({"component": "policy", "action": cfg["unsaved"]["action"],
                               "engine": cfg["summary"]["engine"]})
            if needs_timer and "scheduler" not in wanted and "scheduler" not in args.skip:
                wanted.append("scheduler")  # reminders / summaries must happen on time
            continue
        try:
            changes = _plan(rt, name, args)
        except RuntimeError as e:
            say("\n[%s] NOT set up: %s" % (name, e))
            report.append({"component": name, "skipped": str(e), "failed": [str(e)]})
            continue
        if not changes:
            why = {"claude": "claude CLI not found on PATH", "codex": "codex not installed",
                   "scheduler": "no launchd/systemd here",
                   "menubar": "needs a Mac with the Xcode command line tools (xcode-select --install)"}.get(name) if not _present(rt, name) else "already done"
            if why != "already done" or name in (args.components or []):
                say("\n[%s] %s" % (name, why))
            report.append({"component": name, "results": [why]})
            continue
        if interactive and not all(ch.noop for ch in changes):
            # people get one plain question; the exact file changes are one keypress away
            say("\n" + QUESTIONS.get(name, "Set up %s?" % name))
            while True:
                ans = _ask("  [Y/n/d]  (Enter = yes, n = skip, d = show the exact changes) ", "y")
                if ans and ans.lower().startswith("d"):
                    for ch in changes:
                        _render(rt, ch, say)
                    continue
                break
            if not ans or not ans.lower().startswith("y"):
                say("  skipped")
                report.append({"component": name, "declined": True})
                continue
        else:
            say("\n[%s]" % name)
            for ch in changes:
                _render(rt, ch, say)
        if all(ch.noop for ch in changes):
            report.append({"component": name, "changes": [c.to_json() for c in changes], "results": ["already done"]})
            continue
        if args.dry_run:
            report.append({"component": name, "changes": [c.to_json() for c in changes], "dry_run": True})
            continue
        try:
            results = base.apply(rt, [c for c in changes if not c.noop])
        except base.ConflictError as e:
            say("  ! %s" % e)
            report.append({"component": name, "error": str(e), "failed": [str(e)]})
            continue
        failed = [r for r in results if r.startswith("FAILED")]
        if interactive and not failed:
            say("  ✓ done")
        else:
            for r in results:
                say("    " + r)
        report.append({"component": name, "results": results, "failed": failed})
    if not args.dry_run:
        config_mod.save(rt, cfg)
    failures = [f for r in report for f in r.get("failed", [])]
    if args.json:
        out(json.dumps({"schema": "chainlog.setup/1", "applied": not args.dry_run, "ok": not failures,
                        "components": report}, indent=2))
        return 2 if no_consent else (1 if failures else 0)
    if no_consent:
        say("\nNothing was changed: there is no terminal to ask for consent. Show this plan to the user,"
            "\nthen run the same command with --yes (and --policy …) once they agree.")
        return 2
    if failures:
        say("\n%d step%s FAILED (see above). Fix the cause and run setup again; finished steps are kept."
            % (len(failures), "" if len(failures) == 1 else "s"))
    from chainlog.doctor import codex_hook_trusted
    wrote_hook = any(r.get("component") == "codex" and any("written" in x and "hooks.json" in x for x in r.get("results", []))
                     for r in report)
    if not args.dry_run and wrote_hook and not codex_hook_trusted(rt):
        say("\nOne more step for Codex: open `codex`, type /hooks, and approve the chainlog entry (only once).")
    if not args.dry_run:
        say("Check everything with: chainlog doctor")
    return 1 if failures else 0


def uninstall(rt, args, out):
    m = base.load_manifest(rt)
    entries = list(reversed(m.get("entries") or []))
    if not entries and not args.purge:
        out("chainlog is not set up, so there is nothing to remove. (To delete your notes too: chainlog uninstall --purge)")
        return 0
    if not args.yes:
        if not rt.isatty():
            out("chainlog uninstall needs --yes when there is no terminal to confirm in (%d recorded changes%s)."
                % (len(entries), ", and --purge deletes all data" if args.purge else ""))
            return 2
        if args.purge:
            out("This turns chainlog off AND deletes all your chainlog notes and the chainlog command.")
        else:
            out("This turns chainlog off: it removes what setup added to Claude Code, Codex, Terminal and the timer.\n"
                "Your notes and the `chainlog` command stay, so you can still read old notes.")
        try:
            ans = _ask("Continue? Type y and press Enter (Enter alone = no): ", "n")
        except Aborted:
            return 130
        if not ans or not ans.lower().startswith("y"):
            out("Cancelled: nothing was changed.")
            return 1
    done, problems, removed_dirs = set(), [], set()
    parts_done = []
    for e in entries:
        if e.get("component") == "cli" and not args.purge:
            continue  # the command stays (so notes stay readable) unless everything is purged
        if e.get("component") and e["component"] not in parts_done:
            parts_done.append(e["component"])
        if e.get("command"):
            undo = e.get("undo_command")
            if undo:
                rc, o, err = rt.run(undo, timeout=60)
                if rc != 0:
                    problems.append("could not run: %s" % " ".join(undo))
            continue
        path = e.get("path")
        if not path or path in done:
            continue
        p = Path(path)
        current = base.read_text(p, rt)
        if current is None:
            continue
        allow = bool(e.get("harness"))
        try:
            if base.sha(current) == e.get("after_sha"):
                if e.get("backup"):
                    with open(e["backup"], encoding="utf-8", errors="surrogateescape", newline="") as f:
                        original = f.read()
                    atomic_write(p, original, rt=rt, allow_harness_write=allow)
                    continue  # an older entry for the same file restores further back if it matches
                p.unlink()
                removed_dirs.add(p.parent)
                done.add(path)
                continue
            # changed since install: remove only our part
            if p.name == "hooks.json":
                try:
                    atomic_write(p, comp.remove_codex_entry(current), rt=rt, allow_harness_write=True)
                    out("✓ took chainlog's entry out of %s (you had changed that file, so the rest stays as you left it)" % _home(rt, p))
                except ValueError:
                    problems.append("%s is not valid JSON any more: remove the chainlog SessionEnd entry by hand"
                                    % _home(rt, p))
            elif p.name == ".zshrc" or p == comp.zshrc(rt):
                atomic_write(p, comp.remove_shell_block(current), rt=rt)
                out("✓ took chainlog's lines out of %s (the rest of the file is untouched)" % _home(rt, p))
            else:
                out("! left %s alone: it changed after setup; remove chainlog's part by hand if you want" % _home(rt, p))
        except OSError as err:
            problems.append("%s: %s" % (_home(rt, p), err))
        done.add(path)
    for d in sorted(removed_dirs, key=lambda x: len(str(x)), reverse=True):
        try:
            if "chainlog" in d.name and not any(d.iterdir()):
                d.rmdir()  # e.g. ~/.codex/skills/chainlog-save/
        except OSError:
            pass
    base.save_manifest(rt, {"v": 1, "entries": []})
    if args.purge:
        link = rt.home / ".local" / "bin" / "chainlog"
        if os.path.islink(link) and os.path.realpath(link).startswith(os.path.realpath(str(rt.data_home))):
            os.unlink(link)
            out("removed  %s" % _home(rt, link))
        shutil.rmtree(rt.data_home, ignore_errors=True)
        out("deleted  %s" % _home(rt, rt.data_home))
    labels = {"claude": "disconnected from Claude Code", "codex": "disconnected from Codex",
              "shell": "Terminal reminder removed from ~/.zshrc (rest of the file untouched)",
              "scheduler": "background timer removed", "cli": "chainlog command removed",
              "menubar": "menu bar app removed"}
    for part in parts_done:
        if part in labels:
            out("✓ " + labels[part])
    for msg in problems:
        out("! " + msg)
    if args.purge:
        out("chainlog is removed, notes included.")
    else:
        out("chainlog is turned off. Your notes are kept in %s and `chainlog` still works for reading them.\n"
            "To remove the command too: rm ~/.local/bin/chainlog   (or delete everything: chainlog uninstall --purge)"
            % _home(rt, rt.data_home))
    return 1 if problems else 0
