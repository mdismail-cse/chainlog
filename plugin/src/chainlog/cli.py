"""chainlog command line. argparse + dispatch only; the logic lives in the other modules."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import shutil
import sys
import time

from chainlog import __version__
from chainlog import chain as chain_mod
from chainlog import config as config_mod
from chainlog import delete as delete_mod
from chainlog import keep as keep_mod
from chainlog import menu as menu_mod
from chainlog import query, resolve, resume, retention as retention_mod, review, sweep, ui
from chainlog.index import NotSaved, load, write_events
from chainlog.noteargs import parse as parse_note_args
from chainlog.redact import redact
from chainlog.runtime import Runtime
from chainlog.store.events import NOTE_MAX, new_event

VISIBLE = ("save", "ls", "find", "show", "resume", "chain", "next", "note", "status", "tag", "star", "unstar",
           "keep", "unkeep", "restore", "delete", "retention", "forget", "undo", "review", "config", "setup", "uninstall", "doctor", "scan", "ask", "context")
ALIASES = {"list": "ls", "r": "resume", "chains": "chain"}
HIDDEN = ("_sweep", "_nudge", "_menu", "_ask", "_context", "help")
STATUSES = ("wip", "pass", "fail", "blocked")
UNDOABLE = ("save", "note", "status", "tag", "star", "unstar", "chain.link", "chain.unlink", "forget")


class Exit(Exception):
    def __init__(self, code, message=None):
        self.code = code
        self.message = message


def _p(*a):
    print(*a)
    sys.stdout.flush()


def _err(msg):
    sys.stderr.write("chainlog: %s\n" % msg)


def _text(raw):
    """User text from argv/stdin: invalid UTF-8 (surrogate escapes) becomes U+FFFD, not a crash."""
    return (raw or "").encode("utf-8", "replace").decode("utf-8", "replace")


# --- output helpers ----------------------------------------------------------------------------
def session_json(s):
    out = {k: v for k, v in s.items() if k not in ("hooks", "pid", "last_save_event", "last_save_at", "snap")}
    out["schema"] = "chainlog.session/1"
    p = resume.plan(s)
    out["resume"] = {"cwd": p["cwd"], "argv": p["argv"], "command": resume.shell_command(p),
                     "warnings": p["notes"] + p["blockers"]}
    return out


def print_rows(rt, st, sessions, now, start=1, widths=None):
    refs = []
    short_w, mid_w = widths or ui.layout(sessions)
    for i, s in enumerate(sessions, start):
        _p(ui.row(st, i, s, now, short_w=short_w, mid_w=mid_w))
        refs.append(s["ref"])
    return refs


def _ask(prompt):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        _p("")
        return None


def pick(rt, st, candidates, prompt="Which session?", enter_default=True):
    """Numbered picker with previews. Returns a session or None."""
    if not candidates:
        _p("no sessions to choose from — chainlog lists what is indexed")
        return None
    now = rt.now()
    _p("? %s%s" % (prompt, " (Enter = 1, q = cancel)" if enter_default else ""))
    for i, c in enumerate(candidates, 1):
        s = c["session"]
        when = s["times"].get("ended_at") or s["times"].get("last_activity_at")
        _p("  %s  %-6s %s  \"%s\"  %s" % (st.bold(str(i)), s["harness"], s["short"], ui.fit(s["title"]["value"], 60),
                                          st.dim(("ended " if not s.get("live") else "active ") + ui.ago_phrase(when, now))))
        if c.get("reason") and c["reason"] != "recent":
            _p("     %s %s" % (st.dim("why:      "), c["reason"]))
        if s.get("last_work"):
            _p("     %s \"%s\"" % (st.dim("last work:"), ui.fit(ui.safe(s["last_work"]), 100)))
    for _attempt in range(3):
        ans = _ask("  › ")
        if ans is None or ans.strip().lower() in ("q", "quit"):
            return None
        ans = ans.strip()
        if not ans and enter_default:
            return candidates[0]["session"]
        if ans.isdigit() and 1 <= int(ans) <= len(candidates):
            return candidates[int(ans) - 1]["session"]
        _p("  type a number from 1 to %d, Enter for 1, or q to cancel" % len(candidates))
    return None


def _candidates_json(error, cands, hint):
    return json.dumps({"error": error, "hint": hint, "candidates": [
        {"ref": c["session"]["ref"], "short": c["session"]["short"], "title": c["session"]["title"]["value"],
         "reason": c.get("reason"), "last_work": c["session"].get("last_work"),
         "ended": c["session"]["times"].get("ended_at") or c["session"]["times"].get("last_activity_at")}
        for c in cands]}, indent=2)


def _interactive(rt, args):
    """Offer "type a number to open it" only to a person at a real terminal (never to scripts,
    pipes, --json or AI agents)."""
    return rt.isatty() and not getattr(args, "json", False) and not rt.env.get("CHAINLOG_NO_PROMPT")


def _choose(rt, st, index, refs):
    """After a list: a row number opens that session; any other text searches and shows new rows;
    Enter quits."""
    while True:
        ans = _ask(st.bold("› ") + "Type a number to open it, or words to search (Enter = quit): ")
        if ans is None:
            return 0
        ans = ans.strip()
        if not ans or ans.lower() in ("q", "quit", "exit"):
            return 0
        num = ans.lstrip("@")
        if num.isdigit() and 1 <= int(num) <= len(refs) and refs[int(num) - 1] in index.sessions:
            return _do_resume(rt, index.sessions[refs[int(num) - 1]])
        hits = query.find(index, _text(ans), limit=15)  # e.g. a PR number or a word
        if not hits:
            _p("  nothing matches %r — try other words, or press Enter to quit" % ans)
            continue
        refs = [h["ref"] for h in hits]
        print_rows(rt, st, hits, rt.now())
        resolve.save_last_list(rt, refs)


def one(index, ref, as_json=False, hint="use a longer id or the full chain key"):
    hits = resolve.resolve_ref(index, ref)
    if not hits:
        if ref.startswith("@"):
            raise Exit(1, "no row %s in the last list (run chainlog, chainlog ls or chainlog find first)" % ref)
        raise Exit(1, "no session matches %r (try: chainlog find %s)" % (ref, ref))
    if len(hits) > 1:
        st = ui.Style(index.rt.color())
        if index.rt.isatty() and not as_json:
            s = pick(index.rt, st, [{"session": h} for h in hits[:9]], "%r matches %d sessions" % (ref, len(hits)))
            if s:
                return s
            raise Exit(1, "cancelled")
        if as_json:
            _p(_candidates_json("ambiguous", [{"session": h} for h in hits[:8]], hint))
            raise Exit(2)
        raise Exit(2, "%r is ambiguous: %s — use a longer id" % (
            ref, ", ".join("%s %s" % (h["harness"], h["short"]) for h in hits[:8])))
    return hits[0]


# --- commands ----------------------------------------------------------------------------------
def cmd_default(rt, args):
    cfg = config_mod.load(rt)
    index = load(rt, cfg)
    if sweep.run(index, mode="cmd"):
        index = load(rt, cfg)  # the sweep wrote something: show it
    st = ui.Style(rt.color())
    now = rt.now()
    if not index.visible():
        _p("No Claude Code or Codex sessions yet.")
        _p("Use Claude Code or Codex as usual. When you leave a session (/exit), type:  chainlog save \"what you did\"")
        from chainlog.integrations import base as _base
        if not _base.load_manifest(rt).get("entries"):
            _p("Not set up yet? Run:  chainlog setup")
        return 0
    all_unsaved = review.unsaved(index)
    unsaved = all_unsaved[:5]
    shown = {s["ref"] for s in unsaved}
    recent = [s for s in index.visible() if s["ref"] not in shown][: max(5, args.n - len(unsaved))]
    widths = ui.layout(unsaved + recent)
    refs = []
    if unsaved:
        _p(st.bold("Unsaved") + st.dim("  (ended without a note — chainlog review)"))
        refs += print_rows(rt, st, unsaved, now, 1, widths)
        if len(all_unsaved) > 5:
            _p(st.dim(" … %d more unsaved — chainlog review (or: chainlog review --auto)" % (len(all_unsaved) - 5)))
        if cfg["unsaved"]["action"] == "list":
            evs = [new_event(rt, "unsaved.handled", s["ref"], "sweep", {"action": "list", "result": "shown"})
                   for s in unsaved if not s.get("unsaved_handled")]
            if evs:
                write_events(rt, evs)
        _p("")
    _p(st.bold("Recent"))
    refs += print_rows(rt, st, recent, now, len(refs) + 1, widths)
    resolve.save_last_list(rt, refs)
    tip = _setup_tip(rt, cfg, index) if rt.isatty() else None  # people at a terminal only, never scripts or agents
    if _interactive(rt, args):
        _p(st.dim(" (● saved · ◐ auto-saved · ○ not saved · ▶ running)"))
        if tip:
            _p(st.yellow(" tip: ") + tip)
        return _choose(rt, st, index, refs)
    _p(st.dim(" (● saved · ◐ auto-saved · ○ not saved · ▶ running)   chainlog resume @N · chainlog save \"note\" · chainlog find TEXT"))
    if tip:
        _p(st.yellow(" tip: ") + tip)
    return 0


def _setup_tip(rt, cfg, index):
    """One line that fixes the biggest gap, or None. Without the Claude Code plugin, `chainlog save`
    can only guess which session you just left. `chainlog config set hints off` silences it."""
    if not cfg.get("hints", True):
        return None
    from chainlog.integrations import components as comp
    if not shutil.which("claude", path=rt.env.get("PATH")) or comp.claude_plugin_installed(rt):
        return None
    if not any(s["harness"] == "claude" for s in index.visible()):
        return None
    return ("Claude Code isn't connected, so chainlog can only guess which session you left. "
            "Fix it in 30 seconds: chainlog setup claude")


def _status_arg(value):
    if not value:
        return None
    v = value.lower()
    if v not in STATUSES:
        raise Exit(1, "status must be one of %s (got %r)" % (", ".join(STATUSES), value))
    return v


def cmd_save(rt, args):
    cfg = config_mod.load(rt)
    st = ui.Style(rt.color())
    if args.args_stdin:
        parsed = parse_note_args(_text(rt.stdin.read()))
    else:
        parsed = {"note": _text(" ".join(args.note or [])).strip(), "status": None, "chain": None, "tags": [],
                  "no_chain": False}
    status = _status_arg(args.status) or parsed["status"]
    chain_arg = args.chain or parsed["chain"]
    no_chain = args.no_chain or parsed["no_chain"]
    tags = (args.tag or []) + parsed["tags"]
    if not (parsed["note"] or status or tags or args.title or args.url or chain_arg):
        raise Exit(1, 'nothing to save — add a note, for example:  chainlog save "what you did"')
    index = load(rt, cfg)

    reason, kind = "", "exact"
    if args.id:
        s = one(index, args.id, as_json=args.json or bool(args.via), hint="re-run with a longer --id <short>")
        reason = "chosen by --id"
    elif args.pick:
        s = pick(rt, st, [{"session": x} for x in index.visible()[:8]], "Which session?")
        if not s:
            raise Exit(1, "cancelled")
        reason = "picked"
    else:
        kind, cands = resolve.which(index, via=args.via)
        if kind in ("exact", "strong", "weak"):
            s, reason = cands[0]["session"], cands[0]["reason"]
        else:
            if args.json or args.via or not rt.isatty():
                sys.stderr.write("chainlog: not sure which session you mean — ask the user, then re-run with "
                                 "--id <short> (candidates below)\n")
                _p(_candidates_json("ambiguous" if cands else "no-session", cands,
                                    "ask the user which one, then re-run the same command with --id <short>"))
                return 2
            s = pick(rt, st, cands, "Which session?" if kind == "many" else "No session ended here recently. Which one?")
            if not s:
                raise Exit(1, "cancelled")
            reason = "picked"

    if (kind in ("strong", "weak") or args.id) and s["saved"] == "user" and rt.isatty() and not args.json:
        last = next((n["text"] for n in reversed(s["notes"]) if n.get("kind") == "note"), "")
        ans = _ask('  This session already has a note ("%s").\n  Add another note to it? [Y/n] ' % ui.fit(ui.safe(last), 50))
        if ans is None or ans.strip().lower().startswith("n"):
            raise Exit(1, "cancelled — to pick another session: chainlog save --pick \"note\"")
    raw_note = parsed["note"]
    trimmed = len(raw_note) > NOTE_MAX
    note, n_red = redact(raw_note) if raw_note else ("", 0)  # redact first: a token must not straddle the cut
    note = note[:NOTE_MAX]
    data = {}
    if note:
        data["note"] = note
    if status:
        data["status"] = status
    if tags:
        data["tags"] = tags
    if args.title:
        data["title"] = redact(_text(args.title))[0]
    if args.url:
        data["url"] = args.url
    chain_src = None
    if not no_chain:
        if chain_arg:
            data["chain"], chain_src = chain_mod.key_from_arg(chain_arg, s, index.chains), "flag"
        elif not s["chains"] and cfg.get("auto_chain", True):
            hinted = next((h["chain"] for h in reversed(s["hooks"]) if h.get("chain")), None)
            if hinted:
                data["chain"], chain_src = chain_mod.key_from_arg(hinted, s, index.chains), "CHAINLOG_CHAIN"
            else:
                key, src = chain_mod.suggest_key(s)
                if key:
                    data["chain"], chain_src = key, src
    if chain_src:
        data["chain_source"] = chain_src
    # remember what the session looked like, so the bookmark survives Claude deleting the transcript
    data["snap"] = {k: v for k, v in (("title", s["title"]["value"]), ("cwd", s.get("cwd")),
                                      ("started_at", s["times"].get("started_at")),
                                      ("branch", (s.get("git") or {}).get("branch")),
                                      ("has_prompt", bool(s.get("has_prompt")))) if v}
    ev = new_event(rt, "save", s["ref"], args.via or "cli", data)
    evs = [ev]
    if chain_src == "flag":
        # an explicit -c replaces chains that were linked automatically (pr-link / title / branch)
        evs = [new_event(rt, "chain.unlink", s["ref"], args.via or "cli", {"chain": c["id"]})
               for c in s["chains"] if c["id"] != data["chain"] and c.get("source") not in ("flag", "user")] + evs
    where = write_events(rt, evs)
    kept_now = _auto_keep(rt, cfg, s)
    after = load(rt, cfg)
    fresh = after.sessions.get(s["ref"], s)
    if args.json:
        _p(json.dumps({"saved": session_json(fresh), "match": {"kind": kind, "reason": reason},
                       "event": ev["id"], "stored": where, "redactions": n_red, "trimmed": trimmed}, indent=2))
        return 0
    used = data.get("chain")
    c = next((x for x in fresh["chains"] if x["id"] == used), None) if used else (fresh["chains"][-1] if fresh["chains"] else None)
    if args.via:  # skills: the model echoes only this one line, so it carries everything
        extras = [x for x in ((c and "%s r%s" % (c["id"], c.get("round", "?"))), status,
                              n_red and "%d secret%s redacted" % (n_red, "" if n_red == 1 else "s"),
                              trimmed and "note trimmed to %d chars" % NOTE_MAX,
                              where == "fallback" and "queued (sandbox)") if x]
        _p("✓ saved %s %s \"%s\"%s" % (fresh["harness"], fresh["short"], ui.fit(fresh["title"]["value"], 60),
                                      "".join(" · " + x for x in extras)))
        return 0
    _p("%s saved  %s %s  \"%s\"%s" % (st.green("✓"), fresh["harness"], fresh["short"],
                                     ui.fit(fresh["title"]["value"], 70),
                                     ("  status " + ui.status_text(st, status)) if status else ""))
    if kept_now:
        _p("  %s  a copy (%s), so Claude's cleanup cannot delete it; chainlog unkeep %s drops it" % (
            st.dim("kept  "), _size_text(kept_now["gz_bytes"]), fresh["short"]))
    if not c and not no_chain and not chain_arg and cfg.get("auto_chain", True):
        conflict = chain_mod.key_conflict(s)
        if conflict:
            _p("  chain  " + st.yellow("not linked: ") + _conflict_text(conflict, fresh["short"]))
    if c:
        src = {"pr-link": "found the PR link", "title": "PR number in the title", "branch": "PR number in the branch",
               "flag": "you chose it", "CHAINLOG_CHAIN": "from CHAINLOG_CHAIN"}.get(chain_src, "already in it")
        _p("  chain  %s · round %s %s%s" % (ui.safe(c["id"]), c.get("round", "?"), st.dim("(%s)" % src),
                                         st.dim("   — wrong PR? chainlog save --id %s -c <PR number>" % fresh["short"])
                                         if chain_src not in (None, "flag") else ""))
    line = "  how    %s" % reason
    if kind == "weak":
        line += st.yellow("   — wrong one? chainlog undo")
    _p(line)
    if len([n for n in fresh["notes"] if n.get("kind") == "note"]) > 1:
        _p(st.dim("  notes  this session now has %d notes (see them: chainlog show %s)"
                  % (len([n for n in fresh["notes"] if n.get("kind") == "note"]), fresh["short"])))
    if n_red:
        _p("  note   %d secret%s redacted" % (n_red, "" if n_red == 1 else "s"))
    if trimmed:
        _p(st.yellow("  note   trimmed to %d characters" % NOTE_MAX))
    if where == "fallback":
        _p(st.dim("  (stored in the fallback spool — the data folder was busy or not writable here; "
                  "the next chainlog command picks it up)"))
    chain = after.chains.get(c["id"]) if c else None
    latest = bool(chain and chain["members"] and chain["members"][-1]["ref"] == fresh["ref"])
    nxt = "chainlog next %s" % shlex.quote(c["id"]) if latest else "chainlog resume %s" % fresh["short"]
    _p("  reopen %s" % ui.safe(nxt))
    return 0


def cmd_ls(rt, args):
    cfg = config_mod.load(rt)
    index = load(rt, cfg)
    out = query.filter_sessions(index, chain=args.chain, pr=args.pr, here=args.here, since=args.since,
                                harness=args.harness, unsaved=args.unsaved, saved=args.saved,
                                include_all=args.all, starred=args.starred, via=args.via, kept=args.kept)
    filtered = any((args.chain, args.pr, args.here, args.since, args.harness, args.unsaved, args.saved, args.starred,
                    args.via, args.kept))
    empty = ("no sessions match these filters" + ("" if args.all else " — try chainlog ls --all")) if filtered \
        else "no sessions indexed yet — run a Claude or Codex session first"
    return _emit_list(rt, out[: args.n], args.json, empty, more=len(out) - args.n, index=index, args=args)


def _emit_list(rt, sessions, as_json, empty_msg, more=0, index=None, args=None):
    if as_json:  # agents' --json calls must not replace the user's @N list
        _p(json.dumps([session_json(s) for s in sessions], indent=2))
        return 0
    resolve.save_last_list(rt, [s["ref"] for s in sessions])
    if not sessions:
        _p(empty_msg)
        return 1
    st = ui.Style(rt.color())
    print_rows(rt, st, sessions, rt.now())
    if more > 0:
        _p(st.dim(" … %d more (use -n %d)" % (more, len(sessions) + more)))
    if index is not None and args is not None and _interactive(rt, args):
        return _choose(rt, st, index, [s["ref"] for s in sessions])
    _p(st.dim(" chainlog resume @N · chainlog show @N"))
    return 0


def cmd_find(rt, args):
    cfg = config_mod.load(rt)
    index = load(rt, cfg)
    text = _text(" ".join(args.text))
    hits = query.find(index, text, limit=args.n + 200)
    empty = "nothing matches %r — try fewer words, a PR number, or chainlog ls" % text
    guess = _command_guess(index, args.text)
    if guess and hits and not args.json:
        sys.stderr.write("chainlog: searching for %r. If you meant a command, try:  chainlog %s\n" % (text, guess))
    elif guess:
        empty = "nothing matches %r — did you mean:  chainlog %s" % (text, guess)
    return _emit_list(rt, hits[: args.n], args.json, empty,
                      more=len(hits) - args.n, index=index, args=args)


def _command_guess(index, words):
    """`chainlog svae "r2 done"` is searched as text. If the first word is a near-miss of a command
    and matches no session by itself, return the command line to suggest, else None."""
    if not words:
        return None
    near = difflib.get_close_matches(words[0].lower(), VISIBLE, n=1, cutoff=0.75)
    if not near or near[0] == words[0].lower() or query.find(index, words[0], limit=1):
        return None
    rest = " ".join(words[1:])
    if near[0] == "save" and rest:
        return 'save "%s"' % rest
    return " ".join([near[0]] + list(words[1:]))


def cmd_show(rt, args):
    index = load(rt)
    s = one(index, args.ref, as_json=args.json)
    p = resume.plan(s, rt=rt)
    cmd = resume.shell_command(p)
    copied = _copy(rt, cmd) if args.copy else None
    if args.json:
        _p(json.dumps(session_json(s), indent=2))
        return 0
    st = ui.Style(rt.color())
    now = rt.now()
    _p("%s %s  %s" % (st.bold(s["harness"]), s["native_id"], st.bold('"%s"' % s["title"]["value"])))
    git = s.get("git") or {}
    _p("  %s  %s%s" % (st.dim("where "), ui.home(rt, s.get("cwd") or "?"),
                       ("  " + ui.safe(git["branch"])) if git.get("branch") else ""))
    t = s["times"]
    where = {"vscode": " in VS Code", "desktop": " in the desktop app"}.get(resume.origin(s)[0], "")
    parts = ["started %s%s" % (ui.ago_phrase(t.get("started_at"), now), where)] if t.get("started_at") else []
    parts.append("last activity %s" % ui.ago_phrase(t.get("last_activity_at"), now))
    if t.get("ended_at"):
        reason = s["end"].get("reason")
        parts.append("ended %s%s" % (ui.ago_phrase(t["ended_at"], now), " (%s)" % reason if reason and reason != "other" else ""))
    if s.get("live"):
        parts.append(st.green("running"))
    _p("  %s  %s" % (st.dim("when  "), " · ".join(parts)))
    _p("  %s  %s%s%s" % (st.dim("saved "), {"user": "yes", "auto": "auto-saved", "none": "no"}[s["saved"]],
                         ("  status " + ui.status_text(st, s["status"])) if s.get("status") else "",
                         ("  " + st.yellow("★ starred")) if s.get("starred") else ""))
    for c in s["chains"]:
        _p("  %s  %s round %s" % (st.dim("chain "), ui.safe(c["id"]), c.get("round", "?")))
    if s["tags"]:
        _p("  %s  %s" % (st.dim("tags  "), " ".join("#" + ui.safe(x) for x in s["tags"])))
    for pr in s["prs"]:
        _p("  %s  %s" % (st.dim("pr    "), ui.safe(pr.get("url") or "%s#%s" % (pr.get("repo"), pr.get("number")))))
    for u in s.get("urls") or []:
        _p("  %s  %s" % (st.dim("url   "), ui.safe(u)))
    for n in s["notes"]:
        _p("  %s  %s %s" % (st.dim("note  " if n.get("kind") == "note" else "summ. "), ui.safe(n["text"]),
                            st.dim("(%s)" % ui.ago_phrase(n["at"], now))))
    for line in ui.preview_lines(st, s, indent="  "):
        _p(line)
    if s.get("stats") and s["stats"].get("cost_usd"):
        x = s["stats"]
        _p("  %s  $%.2f · +%s/−%s lines" % (st.dim("stats "), x["cost_usd"] or 0, x.get("lines_added") or 0,
                                            x.get("lines_removed") or 0))
    m = keep_mod.meta(rt, s)
    if m:
        _p("  %s  a copy (%s) is kept%s" % (st.dim("kept  "), _size_text(m["gz_bytes"]),
                                           "" if s["transcript"].get("present") else ": Claude's original is gone, resuming restores it"))
    left = keep_mod.days_left(rt, s)
    if left is not None and left < 7 and not m:
        p["notes"].append("Claude deletes this transcript in about %d day%s (cleanupPeriodDays); keep a copy with: chainlog keep %s "
                          "(or lengthen it: chainlog retention DAYS)" % (max(0, left), "" if int(left) == 1 else "s", s["short"]))
    for msg in p["notes"] + p["blockers"]:
        _p("  " + st.yellow("! " + msg))
    tail = "" if copied is None else (st.dim("   (copied)") if copied else st.yellow("   (could not copy: no clipboard tool)"))
    _p("  %s  %s%s" % (st.dim("resume"), ui.safe(cmd), tail))
    return 0


def _copy(rt, text):
    for tool in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(tool[0], path=rt.env.get("PATH")):
            rc, _o, _e = rt.run(tool, input=text, timeout=3)
            return rc == 0
    return False


def _do_resume(rt, s, fork=False, print_only=False, here=False):
    p = resume.plan(s, fork=fork, here=here, rt=rt)
    st = ui.Style(rt.color())
    for b in p["blockers"]:
        _err(b)
    if p["blockers"]:
        return 1
    for n in p["notes"]:
        sys.stderr.write(st.yellow("! ") + n + "\n")
    if print_only:
        _p(ui.safe(resume.shell_command(p)))
        return 0
    if s.get("live") and not fork and rt.isatty():
        ans = _ask("  open a second copy anyway? [y/N] ")
        if not ans or ans.strip().lower() != "y":
            return 1
    if p.get("restore"):
        try:
            keep_mod.restore(rt, s)
        except keep_mod.KeepError as e:
            raise Exit(1, str(e))
        sys.stderr.write(st.dim("→ Claude had deleted it; restored your kept copy\n"))
    sys.stderr.write(st.dim("→ %s\n" % resume.shell_command(p)))
    try:
        resume.execute(p)
    except OSError as e:
        raise Exit(1, "could not start %s: %s" % (p["argv"][0], e))
    return 0


def cmd_resume(rt, args):
    index = load(rt)
    return _do_resume(rt, one(index, args.ref), fork=args.fork, print_only=args.print, here=args.here)


def cmd_next(rt, args):
    index = load(rt)
    hits = resolve.resolve_ref(index, args.key)  # chain refs resolve to their newest (or given) round
    if not hits:
        raise Exit(1, "no chain %r (chainlog chain lists them)" % args.key)
    if len(hits) > 1:
        keys = sorted({c["id"] for h in hits for c in h["chains"]})
        raise Exit(2, "%r matches several chains%s — use the full key" % (
            args.key, (": " + ", ".join(keys)) if keys else ""))
    return _do_resume(rt, hits[0], fork=args.fork, print_only=args.print, here=args.here)


def cmd_chain(rt, args):
    index = load(rt)
    st = ui.Style(rt.color())
    now = rt.now()
    if not args.key:
        chains = sorted(index.chains.values(), key=lambda c: c["last_activity"], reverse=True)
        if args.json:
            _p(json.dumps(chains, indent=2))
            return 0
        if not chains:
            _p("no chains yet — save with a PR link/title, or: chainlog save \"note\" -c repo#123")
            return 0
        w = max(ui.cells(c["id"]) for c in chains)
        for c in chains:
            n = len(c["members"])
            _p(" %s  %2d round%s  %s  %s  %s" % (ui.pad(ui.safe(c["id"]), w), n, " " if n == 1 else "s",
                                                ui.pad(ui.status_text(st, c.get("status")) or st.dim("—"), 8),
                                                ui.fit(ui.safe(c["title"]), 50), st.dim(ui.ago(c["last_activity"], now))))
        return 0
    try:
        c = resolve.find_chain(index, args.key)
    except ValueError as e:
        raise Exit(2, "%s — use the full key" % e)
    if not c:
        # not a chain key: a session ref -> the chain that session belongs to
        hits = resolve.resolve_ref(index, args.key)
        cid = next((x["id"] for x in hits[0]["chains"]), None) if len(hits) == 1 else None
        c = index.chains.get(cid) if cid else None
    if not c:
        raise Exit(1, "no chain %r (chainlog chain lists them)" % args.key)
    if args.json:
        _p(json.dumps(dict(c, members=[dict(m, session=session_json(index.sessions[m["ref"]])) for m in c["members"]]),
                      indent=2))
        return 0
    _p("%s · %d round%s · %s%s" % (st.bold(c["id"]), len(c["members"]), "" if len(c["members"]) == 1 else "s",
                                   ui.status_text(st, c.get("status")) or "no status", " · " + c["url"] if c.get("url") else ""))
    members = [index.sessions[m["ref"]] for m in c["members"]]
    w = max(len(s["short"]) for s in members)
    refs = []
    for m, s in zip(c["members"], members):
        when = s["times"].get("started_at") or s["times"].get("last_activity_at")
        day = time.strftime("%b %d", time.localtime(when)) if when else "?"
        mark = st.green("▶") if s.get("live") else " "
        _p(" %s %s %s  %-6s %s  %s %s" % (st.dim("@%-2d" % m["round"]), mark, st.dim(ui.pad(s["short"], w)), s["harness"], day,
                                          ui.pad(ui.status_text(st, s.get("status")) or st.dim("—"), 8),
                                          ui.fit(ui.headline(s), 70)))
        refs.append(s["ref"])
    resolve.save_last_list(rt, refs)
    if _interactive(rt, args):
        _p(st.dim(" @N = round N"))
        return _choose(rt, st, index, refs)
    _p(st.dim(" @N = round N · reopen the latest round: chainlog next %s" % c["id"]))
    return 0


def _write_simple(rt, args, etype, data, label):
    index = load(rt)
    s = one(index, args.ref)
    write_events(rt, [new_event(rt, etype, s["ref"], "cli", data)])
    _p("%s %s %s %s \"%s\"" % (ui.Style(rt.color()).green("✓"), label, s["harness"], s["short"],
                              ui.fit(s["title"]["value"], 50)))
    return 0


def cmd_note(rt, args):
    text, _n = redact(_text(" ".join(args.text))[:NOTE_MAX])
    return _write_simple(rt, args, "note", {"text": text}, "noted")


def cmd_status(rt, args):
    value = _status_arg(args.value)
    return _write_simple(rt, args, "status", {"value": value}, "status %s:" % value)


def cmd_tag(rt, args):
    add = [t.lstrip("+") for t in args.tags if not t.startswith("-") and t.lstrip("+")]
    rem = [t[1:] for t in args.tags if t.startswith("-") and t[1:]]
    if not add and not rem:
        raise Exit(1, "usage: chainlog tag REF +add -remove")
    bad = [t for t in add + rem if not re.match(r"^[A-Za-z0-9][\w.-]{0,30}$", t)]
    if bad:
        raise Exit(1, "tags are words of letters, digits, '.', '_' or '-': %s" % ", ".join(map(repr, bad)))
    label = "tagged %s" % " ".join(["+" + t for t in add] + ["-" + t for t in rem])
    return _write_simple(rt, args, "tag", {"add": add, "remove": rem}, label + ":")


def cmd_star(rt, args, on=True):
    """Star (favourite) sessions so they stay in the Starred list; unstar takes them out again."""
    index = load(rt)
    st = ui.Style(rt.color())
    seen = set()
    for ref in args.refs:
        s = one(index, ref)
        if s["ref"] in seen:
            continue
        seen.add(s["ref"])
        what = "starred" if on else "unstarred"
        if bool(s.get("starred")) == on:
            _p("%s already %s %s %s \"%s\"" % (st.dim("="), what, s["harness"], s["short"], ui.fit(s["title"]["value"], 50)))
            continue
        write_events(rt, [new_event(rt, "star" if on else "unstar", s["ref"], "cli", {})])
        _p("%s %s %s %s \"%s\"" % (st.yellow("★") if on else st.green("✓"), what, s["harness"], s["short"],
                                    ui.fit(s["title"]["value"], 50)))
        m = _auto_keep(rt, config_mod.load(rt), s) if on else None
        if m:
            _p("  %s  a copy (%s), so Claude's cleanup cannot delete it" % (st.dim("kept  "), _size_text(m["gz_bytes"])))
    return 0


def cmd_unstar(rt, args):
    return cmd_star(rt, args, on=False)


def _size_text(n):
    return "%.1f MB" % (n / 1e6) if n >= 1e6 else "%d KB" % max(1, round(n / 1e3)) if n else "0 KB"


def _auto_keep(rt, cfg, s):
    """Saving or starring a session means you want it: keep a copy Claude's cleanup cannot delete (config keep.auto).
    Returns the stored details, or None when nothing was kept. Never makes the command fail."""
    if not (cfg.get("keep") or {}).get("auto", True) or s["harness"] != "claude" or s.get("kept") and keep_mod.has_copy(rt, s):
        return None
    try:
        m = keep_mod.keep(rt, s)
    except keep_mod.KeepError:
        return None  # e.g. Claude already deleted it: nothing to copy
    write_events(rt, [new_event(rt, "keep", s["ref"], "cli", {"bytes": m["gz_bytes"], "auto": True})])
    return m


def cmd_keep(rt, args):
    """Keep a copy of sessions so Claude's cleanup (cleanupPeriodDays) cannot delete them."""
    index = load(rt)
    st = ui.Style(rt.color())
    if args.refresh:
        done, failed = keep_mod.refresh(rt, index.sessions.values())
        for ref, why in failed:
            _err("%s: %s" % (ref, why))
        if not args.quiet:
            _p("refreshed %d kept cop%s" % (len(done), "y" if len(done) == 1 else "ies") if done else "kept copies are up to date")
        return 1 if failed else 0
    if not args.refs:
        raise Exit(1, "usage: chainlog keep REF [REF …]   (chainlog ls --kept lists them; --refresh updates the copies)")
    code, seen = 0, set()
    for ref in args.refs:
        s = one(index, ref)
        if s["ref"] in seen:
            continue
        seen.add(s["ref"])
        try:
            m = keep_mod.keep(rt, s)
        except keep_mod.KeepError as e:
            _err("%s %s: %s" % (s["harness"], s["short"], e))
            code = 1
            continue
        if not s.get("kept"):
            write_events(rt, [new_event(rt, "keep", s["ref"], "cli", {"bytes": m["gz_bytes"]})])
        _p("%s kept  %s %s  \"%s\"  %s → %s" % (st.green("✓"), s["harness"], s["short"], ui.fit(s["title"]["value"], 50),
                                              _size_text(m["size"]), _size_text(m["gz_bytes"])))
        left = keep_mod.days_left(rt, s)
        if left is not None:
            _p(st.dim("  Claude deletes the original in about %d day%s; the copy stays until: chainlog unkeep %s"
                      % (max(0, left), "" if int(left) == 1 else "s", s["short"])))
    return code


def cmd_unkeep(rt, args):
    """Delete the kept copy of sessions (the originals are not touched)."""
    index = load(rt)
    st = ui.Style(rt.color())
    code = 0
    for ref in dict.fromkeys(args.refs):
        s = one(index, ref)
        if not (s.get("kept") or keep_mod.has_copy(rt, s)):
            _p("%s not kept  %s %s \"%s\"" % (st.dim("="), s["harness"], s["short"], ui.fit(s["title"]["value"], 50)))
            continue
        if not s["transcript"].get("present") and not args.yes:
            # Claude's original is gone: this copy is all that is left of the conversation
            if not rt.isatty():
                _err("%s %s: Claude's original is gone, so this copy is all that is left; add --yes to delete it" % (s["harness"], s["short"]))
                code = 1
                continue
            ans = _ask("  %s %s: Claude's original is gone, so this copy is all that is left. Delete it? [y/N] " % (s["harness"], s["short"]))
            if not ans or ans.strip().lower() != "y":
                continue
        freed = keep_mod.remove(rt, s)
        write_events(rt, [new_event(rt, "unkeep", s["ref"], "cli", {})])
        _p("%s no longer kept  %s %s  \"%s\"  (%s freed)" % (st.green("✓"), s["harness"], s["short"],
                                                           ui.fit(s["title"]["value"], 50), _size_text(freed)))
    return code


def cmd_restore(rt, args):
    """Put a kept copy back where Claude looks for it (resume does this by itself)."""
    index = load(rt)
    s = one(index, args.ref)
    try:
        dest = keep_mod.restore(rt, s)
    except keep_mod.KeepError as e:
        raise Exit(1, str(e))
    _p("restored your kept copy of %s %s" % (s["harness"], s["short"]) if dest else "%s %s is already where Claude keeps it" % (s["harness"], s["short"]))
    return 0


def cmd_delete(rt, args):
    """Delete sessions from Claude (its transcript), from chainlog (the kept copy, hidden from lists), or both."""
    index = load(rt)
    st = ui.Style(rt.color())
    sessions = []
    for ref in args.refs:
        s = one(index, ref)
        if all(s["ref"] != x["ref"] for x in sessions):
            sessions.append(s)
    plans = [(s, delete_mod.plan(rt, s, args.where)) for s in sessions]
    refused = [(s, p) for s, p in plans if p["refused"]]
    for s, p in refused:
        _err("%s %s: %s" % (s["harness"], s["short"], p["refused"]))
    if refused:
        return 1  # nothing is deleted unless every session can be
    for s, p in plans:
        _p("%s %s  \"%s\"" % (st.bold(s["harness"]), s["short"], ui.fit(s["title"]["value"], 60)))
        for path, n in p["claude"]:
            _p("  %s  %s  (%s)" % (st.dim("Claude "), ui.home(rt, path), _size_text(n)))
        if args.where in ("both", "claude") and s["harness"] == "claude" and not p["claude"]:
            _p("  %s  nothing left (it is already deleted)" % st.dim("Claude "))
        if p["kept"]:
            _p("  %s  %s" % (st.dim("kept   "), _size_text(p["kept"])))
        if p["chainlog"]:
            _p("  %s  hidden from every list (chainlog undo brings it back); its notes stay in the log" % st.dim("chainlog"))
    if not args.yes:
        if not rt.isatty():
            raise Exit(1, "this deletes files and cannot be undone; add --yes to do it without asking")
        ans = _ask("Delete %s? This cannot be undone. [y/N] " % ("this session" if len(sessions) == 1 else "these %d sessions" % len(sessions)))
        if not ans or ans.strip().lower() != "y":
            _p("nothing deleted")
            return 1
    for s, p in plans:
        try:
            freed = delete_mod.carry_out(rt, s, args.where)
        except OSError as e:
            _err("%s %s: could not delete everything: %s" % (s["harness"], s["short"], e))
            return 1
        if p["chainlog"]:
            evs = [new_event(rt, "unkeep", s["ref"], "cli", {})] if s.get("kept") else []
            if not s.get("hidden"):
                evs.append(new_event(rt, "forget", s["ref"], "cli", {"deleted": True}))
            if evs:
                write_events(rt, evs)
        _p("%s deleted  %s %s  (%s from Claude, %s of kept copy)" % (st.green("✓"), s["harness"], s["short"],
                                                                    _size_text(freed["claude_bytes"]), _size_text(freed["kept_bytes"])))
    return 0


def cmd_retention(rt, args):
    """Show or set how long Claude keeps transcripts (cleanupPeriodDays)."""
    index = load(rt)
    st = ui.Style(rt.color())
    text = retention_mod.read(rt)
    try:
        json.loads(text or "{}")
    except ValueError:
        raise Exit(1, "%s is not valid JSON, so chainlog will not touch it (fix it, then run this again)" % ui.home(rt, retention_mod.path(rt)))
    days = retention_mod.current(text)
    shown = days or retention_mod.DEFAULT
    rows = [(s, keep_mod.days_left(rt, s)) for s in index.visible(include_empty=True, include_hidden=True)
            if s["harness"] == "claude" and s["transcript"].get("present")]
    rows = [(s, d) for s, d in rows if d is not None]
    if args.days is None and not args.default:
        _p("Claude keeps a transcript for %s after the session was last used." % st.bold("%d days" % shown))
        _p(st.dim("  cleanupPeriodDays in %s %s" % (ui.home(rt, retention_mod.path(rt)),
                                                    "(set by you)" if days else "is not set: Claude's own default is %d" % retention_mod.DEFAULT)))
        if rows:
            soon = [(s, d) for s, d in rows if d < 14]
            _p("  %d sessions are on disk; the oldest goes in %d days" % (len(rows), max(0, min(d for _s, d in rows))))
            if soon:
                unkept = [s for s, _d in soon if not keep_mod.has_copy(rt, s)]
                _p("  %s %d expire within 14 days (%d without a kept copy)" % (st.yellow("!"), len(soon), len(unkept)))
        _p(st.dim("  Change it: chainlog retention DAYS     Keep one for good: chainlog keep REF"))
        return 0
    new = None if args.default else args.days
    if new is not None and not 1 <= new <= retention_mod.MAXIMUM:
        raise Exit(1, "days must be between 1 and %d" % retention_mod.MAXIMUM)
    try:
        after = retention_mod.edit(text, new)
    except ValueError as e:
        raise Exit(1, "%s: %s" % (ui.home(rt, retention_mod.path(rt)), e))
    target = new or retention_mod.DEFAULT
    doomed = [s for s, d in rows if d is not None and d - shown + target <= 0] if target < shown else []
    if args.json:  # a plan for the menu bar app: nothing is written
        _p(json.dumps({"current": shown, "to": target, "changed": after != text, "would_delete": len(doomed),
                       "would_delete_unkept": sum(1 for s in doomed if not keep_mod.has_copy(rt, s))}))
        return 0
    if after == text:
        _p("already %s" % ("%d days" % new if new else "Claude's default (%d days)" % retention_mod.DEFAULT))
        return 0
    import difflib
    name = ui.home(rt, retention_mod.path(rt))
    _p("".join(difflib.unified_diff((text or "").splitlines(keepends=True), after.splitlines(keepends=True), fromfile=name, tofile=name)).rstrip("\n"))
    if doomed:
        lost = [s for s in doomed if not keep_mod.has_copy(rt, s)]
        _p(st.yellow("! %d of your sessions are older than %d days; Claude deletes them the next time it starts (%d have no kept copy: chainlog keep REF first)."
                     % (len(doomed), target, len(lost))))
    if not args.yes:
        if not rt.isatty():
            raise Exit(1, "this edits %s; add --yes to do it without asking" % name)
        ans = _ask("Write this? [y/N] ")
        if not ans or ans.strip().lower() != "y":
            _p("nothing changed")
            return 1
    backup = retention_mod.write(rt, text, after)
    _p("%s Claude will keep transcripts for %d days from now on%s" % (st.green("✓"), target, st.dim("  (old file saved as %s)" % ui.home(rt, str(backup))) if backup else ""))
    return 0


def cmd_forget(rt, args):
    return _write_simple(rt, args, "forget", {}, "forgotten (chainlog undo restores it):")


def cmd_undo(rt, args):
    index = load(rt)
    undone = {e["data"].get("event") for e in index.events if e["type"] == "undo"}
    for _pos, ev in sorted(enumerate(index.events), key=lambda x: (x[1]["ts"], x[0]), reverse=True):
        if ev["type"] in UNDOABLE and ev["id"] not in undone and ev.get("src") != "auto":
            write_events(rt, [new_event(rt, "undo", ev["ref"], "cli", {"event": ev["id"]})])
            s = index.sessions.get(ev["ref"]) or {}
            what = ui.safe(ev["data"].get("note") or ev["data"].get("text") or "")
            _p("↶ undid %s on %s %s \"%s\"%s" % (
                ev["type"], ev["ref"].split(":")[0], s.get("short", ev["ref"][-8:]),
                ui.fit((s.get("title") or {}).get("value", ""), 40), (' — note "%s"' % ui.fit(what, 40)) if what else ""))
            return 0
    _p("nothing to undo")
    return 1


def cmd_review(rt, args):
    cfg = config_mod.load(rt)
    index = load(rt, cfg)
    sessions = review.unsaved(index, include_all=args.all)
    if args.auto:
        return _review_auto(rt, cfg, sessions, args, index.chains)
    if args.json or not rt.isatty():
        if not args.json:
            sys.stderr.write("chainlog: not a terminal, so printing the unsaved sessions as JSON (review is interactive)\n")
        _p(json.dumps([session_json(s) for s in sessions], indent=2))
        return 0
    st = ui.Style(rt.color())

    def save_cb(s, raw):
        parsed = parse_note_args(_text(raw))
        note, _n = redact(parsed["note"][:NOTE_MAX])
        data = {"note": note} if note else {}
        if parsed["status"]:
            data["status"] = parsed["status"]
        if parsed["tags"]:
            data["tags"] = parsed["tags"]
        key = chain_mod.key_from_arg(parsed["chain"], s, index.chains) if parsed["chain"] else (
            None if parsed["no_chain"] or s["chains"] else chain_mod.suggest_key(s)[0])
        if key:
            data["chain"] = key
        data["snap"] = {"title": s["title"]["value"], "cwd": s.get("cwd"), "started_at": s["times"].get("started_at")}
        write_events(rt, [new_event(rt, "save", s["ref"], "cli", data)])
        fresh = load(rt, cfg).sessions.get(s["ref"], s)
        c = fresh["chains"][-1] if fresh["chains"] else None
        return "%s saved%s%s" % (st.green("✓"), " · %s r%s" % (c["id"], c.get("round", "?")) if c else "",
                                 " · " + parsed["status"] if parsed["status"] else "")

    def resume_cb(s):
        _do_resume(rt, s)

    if not sessions:
        _p("nothing to review — every recent session is saved%s" % (
            "" if args.all else " (chainlog review --all includes older ones)"))
        return 0
    review.interactive(index, sessions, st, _ask, lambda evs: write_events(rt, evs), _p, resume_cb, save_cb)
    return 0


def _review_auto(rt, cfg, sessions, args, chains):
    """`chainlog review --auto`: clear the whole backlog at once with automatic summaries."""
    if args.json:
        raise Exit(1, "--auto and --json can't be combined")
    if not sessions:
        _p("nothing to review — every recent session is saved%s" % (
            "" if args.all else " (chainlog review --all includes older ones)"))
        return 0
    st = ui.Style(rt.color())
    n = len(sessions)
    if not args.yes:
        if not rt.isatty():  # a script or an agent: nobody to ask, so nothing is saved without --yes
            _p("chainlog review --auto would save %d unsaved session%s with an automatic summary. There is no terminal "
               "to ask in: run it again with --yes if that is what you want." % (n, "" if n == 1 else "s"))
            return 2
        ans = _ask("Save %d unsaved session%s with an automatic summary? You can still add your own note later. [Y/n] "
                   % (n, "" if n == 1 else "s"))
        if ans is None or ans.strip().lower().startswith("n"):
            _p("Cancelled — nothing was saved.")
            return 1
    done = unlinked = 0
    for s in sessions:
        _summary, _engine, chain, conflict = review.auto_save(rt, cfg, s, lambda evs: write_events(rt, evs), chains)
        done += 1
        _p("  %s %s %s  \"%s\"%s" % (st.cyan("◐"), s["harness"], s["short"], ui.fit(s["title"]["value"], 60),
                                    " → " + ui.safe(chain) if chain else ""))
        if conflict:
            _p(st.yellow("      not linked to a PR: ") + _conflict_text(conflict, s["short"]))
            unlinked += 1
    _p("%s auto-saved %d session%s. Add your own note to any: chainlog save --id <id> \"note\"" % (
        st.green("✓"), done, "" if done == 1 else "s"))
    if unlinked:
        _p(st.yellow("!") + " %d of them could not be linked to a PR because the PR link and the title disagree (see above)."
           % unlinked)
    return 0


def _conflict_text(conflict, short):
    link, text, where = conflict
    return "the PR link says #%d but the %s says #%d. Choose:  chainlog save --id %s -c <PR number> \"note\"" % (
        link, where, text, short)


def cmd_config(rt, args):
    cfg = config_mod.load(rt)
    if args.action in (None, "list"):
        flat = {k: config_mod.get(cfg, k) for k in config_mod.KEYS}
        if args.json:
            _p(json.dumps(flat, indent=2))
        else:
            st = ui.Style(rt.color())
            for k, v in flat.items():
                _p("%-18s %-10s %s" % (k, config_mod.show(k, v), st.dim(config_mod.KEYS[k])))
        return 0
    if not args.key:
        raise Exit(1, "usage: chainlog config %s KEY%s" % (args.action, " VALUE" if args.action == "set" else ""))
    if args.action == "get":
        try:
            v = config_mod.get(cfg, args.key)
            _p(json.dumps(v, indent=2) if isinstance(v, dict) else config_mod.show(args.key, v))
        except KeyError:
            raise Exit(1, "unknown setting %r. Known: %s" % (args.key, ", ".join(sorted(config_mod.KEYS))))
        return 0
    if args.value is None:
        raise Exit(1, "usage: chainlog config set %s VALUE  (%s)" % (args.key, config_mod.KEYS.get(args.key, "?")))
    try:
        cfg = config_mod.set_value(cfg, args.key, args.value)
    except (KeyError, ValueError) as e:
        raise Exit(1, str(e).strip("'\""))
    config_mod.save(rt, cfg)
    _p("✓ %s = %s" % (args.key, config_mod.show(args.key, config_mod.get(cfg, args.key))))
    if args.key.startswith("unsaved."):
        sweep.run(load(rt, cfg), mode="cmd")  # recompute the pending due time with the new setting
    if args.key == "unsaved.action" and args.value in ("summarize", "remind"):
        from chainlog.integrations import components as comp
        if not (comp.scheduler_loaded(rt) and (sys.platform != "darwin" or comp.plist_path(rt).exists())):
            _p("  %s needs the small background timer (it checks every 2 minutes)." % (
                "Reminding" if args.value == "remind" else "Auto-saving"))
            if rt.isatty():
                ans = _ask("  Turn it on now? [Y/n] ")
                if ans is not None and not ans.strip().lower().startswith("n"):
                    from chainlog import setup as setup_mod
                    ns = argparse.Namespace(components=["scheduler"], dry_run=False, yes=True, skip=[], policy=None,
                                            engine=None, local=False, json=False)
                    return setup_mod.run(rt, ns, _p)
            _p("  Turn it on with:  chainlog setup scheduler")
    return 0


def cmd_scan(rt, args):
    t = time.time()
    index = load(rt, force=args.full)
    counts = {}
    for s in index.visible():
        counts[s["harness"]] = counts.get(s["harness"], 0) + 1
    if counts:
        _p("Found %s." % " and ".join("%d %s session%s" % (n, h.capitalize(), "" if n == 1 else "s")
                                      for h, n in sorted(counts.items())))
    else:
        _p("No sessions found yet. They will show up after you use Claude Code or Codex.")
    return 0


def cmd_sweep(rt, args):
    cfg = config_mod.load(rt)
    if not cfg.get("installed_at"):
        return 0
    mode = "tty" if args.tty else ("cron" if args.cron or args.background else "cmd")
    index = load(rt, cfg)
    try:
        keep_mod.refresh(rt, index.sessions.values())  # sessions you kept keep growing: bring the copies up to date
    except Exception:  # never let a copy problem stop the sweep
        pass
    lines = sweep.run(index, mode=mode)
    if mode == "tty":
        st = ui.Style(rt.color())
        for line in lines:
            _p(st.dim(line) if line.startswith("  ") else st.cyan(line))
    return 0


def cmd_nudge(rt, args):
    """Called by the zsh prompt hook right after an agent exits in this terminal."""
    cfg = config_mod.load(rt)
    index = load(rt, cfg)
    now = rt.now()
    pid = args.shell_pid
    best = None
    for s in index.visible(include_empty=True):
        for h in s["hooks"]:
            if h["type"] == "hook.end" and now - h["at"] <= 180 and pid in (h.get("anc") or []):
                if best is None or h["at"] > best[0]:
                    best = (h["at"], s)
    if not best or best[1]["saved"] != "none" or not best[1].get("has_prompt"):
        return 0
    s = best[1]
    st = ui.Style(rt.color())
    dur = ui.duration(s["times"].get("started_at"), s["times"].get("ended_at"))
    key, _src = chain_mod.suggest_key(s)
    action = cfg["unsaved"]["action"]
    after = config_mod.fmt_duration(cfg["unsaved"]["after"])
    then = {"summarize": "if you don't, chainlog auto-saves it in %s" % after,
            "remind": "if you don't, you get a reminder in %s" % after,
            "list": "or later: chainlog review", "off": ""}.get(action, "")
    _p(st.cyan("chainlog ▸ ended unsaved: ") + "%s %s \"%s\"%s%s — %s%s" % (
        s["harness"], s["short"], ui.fit(s["title"]["value"], 50), " · " + dur if dur else "", " · " + key if key else "",
        st.bold('chainlog save "…"'), st.dim("  (%s)" % then) if then else ""))
    return 0


def cmd_menu(rt, args):
    """The Mac menu bar app polls this: sessions, chains and ready-made resume commands as JSON."""
    if (args.starred or args.every_word) and args.find is None:
        raise Exit(2, "--starred and --every-word only narrow a search: use them with --find TEXT")
    index = load(rt, config_mod.load(rt))
    _p(json.dumps(menu_mod.build(index, find=args.find, find_starred=args.starred, find_every_word=args.every_word,
                                 find_limit=200 if args.every_word else 40), indent=None, separators=(",", ":")))
    return 0


def _ask_status_text(st, style):
    lines = []
    for engine, e in st["engines"].items():
        mark = style.green("✓") if e["installed"] and e["signed_in"] is not False else style.dim("·")
        mine = " (in use)" if st["engine"] == engine else ""
        agreed = ", you agreed to send it session text" if engine in st["consented"] else ""
        lines.append("  %s %-7s %s%s%s" % (mark, engine, e["detail"], mine, agreed))
    return lines


def cmd_ask(rt, args):
    """Ask Claude or Codex about your sessions (it runs the `claude` / `codex` you are signed in to; nothing else)."""
    from chainlog import ask as ask_mod
    cfg = config_mod.load(rt)
    st = ui.Style(rt.color())
    if args.disconnect:
        ask_mod.disconnect(rt, cfg)
        _p("Disconnected. chainlog will not send session text to Claude or Codex until you connect again.")
        return 0
    if args.status:
        status = ask_mod.status(rt, cfg)
        if args.json:
            _p(json.dumps(status, separators=(",", ":")))
        else:
            _p("Ask Claude or Codex about your sessions: %s" % ("ready (%s)" % status["engine"] if status["ready"] else "not connected"))
            for line in _ask_status_text(status, st):
                _p(line)
            if not status["ready"]:
                _p("Connect one: chainlog ask --connect claude   (or codex)")
        return 0
    if args.connect:
        engine = args.connect
        _p(ask_mod.disclosure(engine))
        if not args.yes:
            if not rt.isatty():
                raise Exit(2, "this asks for your agreement; run it in a terminal, or add --yes to agree")
            reply = _ask("Connect %s and agree to this? [y/N] " % ask_mod.LABEL[engine])
            if (reply or "").strip().lower() not in ("y", "yes"):
                _p("Cancelled. Nothing was changed.")
                return 1
        try:
            ask_mod.connect(rt, cfg, engine)
        except ask_mod.AskError as e:
            raise Exit(1, e.message)
        _p("%s Connected to %s. Ask something: chainlog ask \"what did I do on the refund PR?\"" % (st.green("✓"), ask_mod.LABEL[engine]))
        return 0
    question = " ".join(args.question).strip()
    if not question:
        if rt.isatty() and not args.json:
            return _ask_chat(rt, cfg, args, ask_mod, st)
        raise Exit(2, "usage: chainlog ask QUESTION   (or: chainlog ask --connect claude|codex, --status, --disconnect)")
    return _ask_once(rt, cfg, args, ask_mod, st, question, [])


def _ask_once(rt, cfg, args, ask_mod, st, question, history):
    index = load(rt, cfg)
    progress = (lambda t: sys.stderr.write(st.dim(t) + "\n")) if rt.isatty() and not args.json else None
    try:
        res = ask_mod.ask(rt, cfg, index, question, history=history, engine=args.engine, progress=progress)
    except ask_mod.AskError as e:
        raise Exit(1, e.message)
    if args.json:
        _p(json.dumps(res, separators=(",", ":")))
        return 0
    _p(ask_mod.plain(res["answer"], res["sessions"]))
    if res["sessions"]:
        _p("")
        for r in res["sessions"]:
            _p("  %s  %s  %s" % (st.dim(r["short"]), ui.fit(r["title"], 60), st.dim(r["ago"])))
        _p(st.dim("  open one: chainlog resume ID"))
    return 0


def _ask_chat(rt, cfg, args, ask_mod, st):
    """`chainlog ask` with no question, in a terminal: a small chat. An empty line ends it."""
    history = []
    _p(st.dim("Ask about your sessions. An empty line ends the chat."))
    while True:
        question = (_ask("you> ") or "").strip()
        if not question:
            return 0
        index = load(rt, cfg)
        try:
            res = ask_mod.ask(rt, cfg, index, question, history=history, engine=args.engine,
                              progress=lambda t: sys.stderr.write(st.dim(t) + "\n"))
        except ask_mod.AskError as e:
            _err(e.message)
            if e.code in ("not_connected", "no_consent", "not_installed", "not_signed_in"):
                return 1
            continue
        _p(ask_mod.plain(res["answer"], res["sessions"]))
        _p("")
        history += [{"role": "user", "text": question}, {"role": "assistant", "text": res["answer"]}]


def cmd_ask_stream(rt, args):
    """What the menu bar app talks to: a JSON request on stdin, one JSON object per line back (status…, then answer or error)."""
    import signal
    from chainlog import ask as ask_mod

    def emit(obj):
        _p(json.dumps(obj, separators=(",", ":")))

    def stop(*_a):  # the app's Stop button: end the request, and the AI process with it (the runner kills its group)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        request = json.loads(rt.stdin.read() or "{}")
        if not isinstance(request, dict):
            raise ValueError("the request must be a JSON object")
        cfg = config_mod.load(rt)
        res = ask_mod.ask(rt, cfg, load(rt, cfg), request.get("question"), history=request.get("history") or [],
                          engine=request.get("engine") or None, progress=lambda t: emit({"type": "status", "text": t}))
        emit(dict(res, type="answer"))
        return 0
    except ask_mod.AskError as e:
        emit({"type": "error", "code": e.code, "message": e.message})
        return 0
    except ValueError as e:
        emit({"type": "error", "code": "bad_request", "message": "chainlog could not read the request: %s" % e})
        return 0

# --- contexts ------------------------------------------------------------------------------------
def _ctx_refs(index, refs, words=None):
    """Sessions from ids/refs (each must match one) and from a search; each listed once, oldest first."""
    out = [one(index, r) for r in refs or []]
    if words:
        out += query.find(index, words, limit=200, every_word=True)  # a near miss would be read and merged for nothing
    seen, uniq = set(), []
    for s in out:
        if s["ref"] not in seen:
            seen.add(s["ref"])
            uniq.append(s)
    return sorted(uniq, key=lambda s: s["times"].get("started_at") or s["times"].get("last_activity_at") or 0)


def _ctx_list_sessions(st, sessions, now, limit=25):
    for s in sessions[:limit]:
        _p("  %s  %s  %s  %s" % (st.dim(s["short"]), time.strftime("%b %d", time.localtime(s["times"].get("last_activity_at") or now)),
                                 ui.pad(s["harness"], 6), ui.fit(ui.headline(s), 64)))
    if len(sessions) > limit:
        _p(st.dim("  … and %d more" % (len(sessions) - limit)))


def _ctx_consent(rt, cfg, args, ctx_mod, engine):
    """Agreement to send whole sessions to `engine` for contexts: asked once, in words (--yes agrees)."""
    if ctx_mod.consented(cfg, engine):
        return cfg
    _p(ctx_mod.disclosure(engine))
    if not args.yes:
        if not rt.isatty():
            raise Exit(2, "building a context asks for your agreement first; run it in a terminal, or add --yes to agree")
        reply = _ask("Agree and build with %s? [y/N] " % ctx_mod.LABEL[engine])
        if (reply or "").strip().lower() not in ("y", "yes"):
            raise Exit(1, "cancelled. Nothing was sent.")
    return ctx_mod.agree(rt, cfg, engine)


def _ctx_build(rt, cfg, args, ctx_mod, index, meta, st):
    try:
        engine = ctx_mod._engine(rt, cfg, args.engine)[0]
    except ctx_mod.AskError as e:
        raise Exit(1, e.message)
    est = ctx_mod.estimate(index, meta)
    if est["requests"] == 0:
        _p("%s is up to date: nothing new to read." % st.bold(meta["name"]))
        return 0
    cfg = _ctx_consent(rt, cfg, args, ctx_mod, engine)
    if not args.json:
        _p(st.dim("Reading %d session%s with %s (about %d requests, ~%dk tokens of reading). Ctrl-C stops; what is read is kept." % (
            est["sessions"], "" if est["sessions"] == 1 else "s", ctx_mod.LABEL[engine], est["requests"], max(1, est["chars"] // 4000))))
    progress = (lambda t, _d=0, _n=0: sys.stderr.write(st.dim("  " + t) + "\n")) if not args.json else None
    try:
        res = ctx_mod.build(rt, cfg, index, meta, engine=engine, progress=progress)
    except ctx_mod.AskError as e:
        raise Exit(1, e.message)
    except KeyboardInterrupt:
        _p("")
        raise Exit(130, "stopped. The sessions read so far are kept: `chainlog context update %s` carries on." % shlex.quote(meta["name"]))
    m = res["meta"]
    if args.json:
        _p(json.dumps(dict(ctx_mod.summary(rt, load(rt, cfg), m), requests=res["requests"]), separators=(",", ":")))
        return 0
    _p("%s %s: %d session%s, %d request%s." % (st.green("✓"), st.bold(m["name"]), len(m["members"]), "" if len(m["members"]) == 1 else "s",
                                               res["requests"], "" if res["requests"] == 1 else "s"))
    if m.get("gist"):
        _p("  " + ui.safe(m["gist"]))
    _p(st.dim("  file: %s" % ctx_mod.files(rt, m)["context"]))
    _p(st.dim("  start a session with it: chainlog context use %s" % shlex.quote(m["name"])))
    return 0


def cmd_context(rt, args):
    """Contexts: what many sessions taught you, in one file a new session reads when it needs it."""
    from chainlog import context as ctx_mod
    cfg = config_mod.load(rt)
    st = ui.Style(rt.color())
    now = rt.now()
    action = args.cx or "ls"
    if action == "questions":
        if args.reset:
            config_mod.save(rt, config_mod._put(cfg, "context.questions", []))
            _p("Questions are back to chainlog's ten.")
        elif args.set:
            try:
                qs = ctx_mod.clean_questions(args.set)
            except ctx_mod.AskError as e:
                raise Exit(2, e.message)
            config_mod.save(rt, config_mod._put(cfg, "context.questions", qs))
            _p("Saved %d question%s as your default for new contexts." % (len(qs), "" if len(qs) == 1 else "s"))
        else:
            qs = ctx_mod.questions_default(cfg)
            if args.json:
                _p(json.dumps({"questions": qs, "default": list(ctx_mod.DEFAULT_QUESTIONS)}))
                return 0
            for i, q in enumerate(qs, 1):
                _p("%2d. %s" % (i, q))
            _p(st.dim("Change them: chainlog context questions --set \"question 1\" \"question 2\" …   (--reset for these ten)"))
        return 0
    index = load(rt, cfg)
    if action == "ls":
        every = ctx_mod.all_contexts(rt)
        if args.json:
            _p(json.dumps([ctx_mod.summary(rt, index, m) for m in every], separators=(",", ":")))
            return 0
        if not every:
            _p("No contexts yet. A context collects what several sessions taught you (say, all your cloud backup work) into one")
            _p("file that a new session reads when it needs it. Make one:")
            _p("  chainlog context new \"Cloud backup\" --find backup     (every session matching the words)")
            _p("  chainlog context new \"Cloud backup\" ID ID ID          (ids from: chainlog find backup)")
            return 0
        w = max(ui.cells(m["name"]) for m in every)
        for m in every:
            sm = ctx_mod.summary(rt, index, m)
            state = ("built %s" % sm["ago"]) if sm["built"] else st.yellow("not built yet")
            todo = sm["pending"]["text"] if sm["built"] else ""
            _p(" %s  %3d session%s  %s%s" % (st.bold(ui.pad(ui.safe(m["name"]), w)), sm["sessions"], " " if sm["sessions"] == 1 else "s", state,
                                             st.yellow(" · " + todo + " → update") if todo else ""))
        _p(st.dim(" start a session with one: chainlog context use NAME   ·   details: chainlog context show NAME"))
        return 0
    if action == "new":
        try:
            name = ctx_mod.clean_name(args.name)
            if args.chain and args.chain not in index.chains:
                raise ctx_mod.AskError("bad_request", "no chain %r (chainlog chain lists them)" % args.chain)
        except ctx_mod.AskError as e:
            raise Exit(2, e.message)
        sessions = _ctx_refs(index, args.refs, " ".join(args.find) if args.find else None)
        if args.chain:
            sessions = _ctx_refs(index, [], None) + sessions
            sessions = sorted({s["ref"]: s for s in sessions + [index.sessions[m["ref"]] for m in index.chains[args.chain]["members"]
                                                               if m["ref"] in index.sessions]}.values(),
                              key=lambda s: s["times"].get("started_at") or 0)
        if not sessions:
            raise Exit(1, "no sessions to put in it: give ids, --find WORDS or --chain KEY (chainlog find WORDS shows ids)")
        if not args.json:
            _p("%s · %d session%s:" % (st.bold(name), len(sessions), "" if len(sessions) == 1 else "s"))
            _ctx_list_sessions(st, sessions, now)
            if args.find and rt.isatty() and not args.yes:
                reply = _ask("Use these? [Y/n] ")
                if reply is None or reply.strip().lower().startswith("n"):
                    raise Exit(1, "cancelled. Pick sessions by id instead: chainlog context new NAME ID ID …")
        try:
            meta = ctx_mod.create(rt, cfg, name, [s["ref"] for s in sessions], chain=args.chain,
                                  questions=args.question or None)
        except ctx_mod.AskError as e:
            raise Exit(1, e.message)
        if args.no_build:
            _p("Saved. Build it when you are ready: chainlog context update %s" % shlex.quote(meta["name"]))
            return 0
        return _ctx_build(rt, cfg, args, ctx_mod, index, meta, st)
    try:
        meta = ctx_mod.get(rt, args.name)
    except ctx_mod.AskError as e:
        raise Exit(1, e.message)
    if action == "update":
        return _ctx_build(rt, cfg, args, ctx_mod, index, meta, st)
    if action == "add":
        sessions = _ctx_refs(index, args.refs, " ".join(args.find) if args.find else None)
        if not sessions:
            raise Exit(1, "no sessions given: ids, or --find WORDS")
        added = ctx_mod.add(rt, meta, [s["ref"] for s in sessions])
        _p("Added %d session%s to %s%s. They are read on the next update: chainlog context update %s" % (
            len(added), "" if len(added) == 1 else "s", st.bold(meta["name"]),
            (" (%d were already in it)" % (len(sessions) - len(added))) if len(sessions) > len(added) else "", shlex.quote(meta["name"])))
        return 0
    if action == "remove":
        refs = [one(index, r)["ref"] if resolve.resolve_ref(index, r) else r for r in args.refs]
        gone = ctx_mod.remove(rt, meta, refs)
        _p("Removed %d session%s from %s. The next update leaves them out." % (len(gone), "" if len(gone) == 1 else "s", st.bold(meta["name"])))
        return 0
    if action == "rm":
        if not args.yes:
            if not rt.isatty():
                raise Exit(2, "this deletes the context's files; add --yes to confirm")
            reply = _ask("Delete the context %s (its notes, not your sessions)? [y/N] " % meta["name"])
            if (reply or "").strip().lower() not in ("y", "yes"):
                raise Exit(1, "cancelled")
        ctx_mod.delete(rt, meta)
        _p("Deleted the context %s. Your sessions are untouched." % st.bold(meta["name"]))
        return 0
    if action == "ref":
        if not meta.get("built_at"):
            raise Exit(1, "build it first: chainlog context update %s" % shlex.quote(meta["name"]))
        text = ctx_mod.reference(rt, meta)
        _p(text)
        if args.copy:
            sys.stderr.write(st.dim("(copied: paste it into any Claude or Codex session)\n") if _copy(rt, text) else "chainlog: could not copy\n")
        return 0
    if action == "use":
        task = " ".join(args.task).strip() or None
        try:
            plan = ctx_mod.launch(rt, index, meta, tool=args.tool, where=args.folder, task=task)
        except ctx_mod.AskError as e:
            raise Exit(1, e.message)
        if args.print or args.json:
            _p(json.dumps(plan, separators=(",", ":")) if args.json else ui.safe(plan["command"]))
            return 0
        sys.stderr.write(st.dim("→ %s session in %s, with the context %s attached\n" % (ctx_mod.LABEL[plan["tool"]], plan["cwd"], meta["name"])))
        try:
            os.chdir(plan["cwd"])
            os.execvp(plan["argv"][0], plan["argv"])
        except OSError as e:
            raise Exit(1, "could not start %s: %s" % (plan["argv"][0], e))
    # show
    sm = ctx_mod.summary(rt, index, meta)
    if args.json:
        _p(json.dumps(sm, separators=(",", ":")))
        return 0
    _p("%s · %d session%s · %s" % (st.bold(meta["name"]), sm["sessions"], "" if sm["sessions"] == 1 else "s",
                                   ("built %s (%s)" % (sm["ago"], sm["period"])) if sm["built"] else "not built yet"))
    if sm["gist"]:
        _p("  " + ui.safe(sm["gist"]))
    if sm["pending"]["text"] and sm["built"]:
        _p(st.yellow("  %s: chainlog context update %s" % (sm["pending"]["text"], shlex.quote(meta["name"]))))
    _p(st.dim("  file: %s" % sm["file"]))
    members = [index.sessions[r] for r in meta["members"] if r in index.sessions]
    _ctx_list_sessions(st, members, now)
    _p(st.dim("  start a session with it: chainlog context use %s   ·   paste into one: chainlog context ref %s --copy" % (
        shlex.quote(meta["name"]), shlex.quote(meta["name"]))))
    return 0


def cmd_context_stream(rt, args):
    """What the menu bar app talks to for building: a JSON request on stdin, one JSON object per line back
    (progress…, then done or error). {"name", "refs"?, "questions"?, "engine"?, "agree"?, "new"?}"""
    import signal
    from chainlog import context as ctx_mod

    def emit(obj):
        _p(json.dumps(obj, separators=(",", ":")))

    def stop(*_a):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        request = json.loads(rt.stdin.read() or "{}")
        if not isinstance(request, dict):
            raise ValueError("the request must be a JSON object")
        cfg = config_mod.load(rt)
        index = load(rt, cfg)
        refs = [r for r in request.get("refs") or [] if isinstance(r, str) and r in index.sessions]
        if request.get("new"):
            meta = ctx_mod.create(rt, cfg, request.get("name"), refs, chain=request.get("chain") or None,
                                  questions=request.get("questions") or None)
        else:
            meta = ctx_mod.get(rt, request.get("name"))
            if refs:
                ctx_mod.add(rt, meta, refs)
            if request.get("questions"):
                meta["questions"] = ctx_mod.clean_questions(request["questions"])
                ctx_mod.save(rt, meta)
        engine = ctx_mod._engine(rt, cfg, request.get("engine") or None)[0]
        if not ctx_mod.consented(cfg, engine):
            if not request.get("agree"):
                emit({"type": "error", "code": "no_consent", "message": ctx_mod.disclosure(engine), "engine": engine})
                return 0
            cfg = ctx_mod.agree(rt, cfg, engine)
        res = ctx_mod.build(rt, cfg, index, meta, engine=engine,
                            progress=lambda t, d=0, n=0: emit({"type": "status", "text": t, "done": d, "total": n}))
        emit({"type": "done", "requests": res["requests"], "up_to_date": res["up_to_date"],
              "context": ctx_mod.summary(rt, load(rt, cfg), res["meta"])})
        return 0
    except ctx_mod.AskError as e:
        emit({"type": "error", "code": e.code, "message": e.message})
        return 0
    except ValueError as e:
        emit({"type": "error", "code": "bad_request", "message": "chainlog could not read the request: %s" % e})
        return 0


def cmd_setup(rt, args):
    from chainlog import setup as setup_mod
    return setup_mod.run(rt, args, _p)


def cmd_uninstall(rt, args):
    from chainlog import setup as setup_mod
    return setup_mod.uninstall(rt, args, _p)


def cmd_doctor(rt, args):
    from chainlog import doctor
    cfg = config_mod.load(rt)
    index = load(rt, cfg, commit=False)
    report = doctor.run(rt, index, cfg)
    if args.json:
        _p(doctor.to_json(report))
    else:
        _p(doctor.render(report, ui.Style(rt.color())))
    return 0 if report["ok"] else 1


# --- parser --------------------------------------------------------------------------------------
def _positive(v):
    try:
        n = int(v)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number")
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


REF_HELP = "REF: id prefix (3f9a1c), @N from the last list, last, a chain (shop#4321, 4321, 4321/2) or a Codex thread name"


def build_parser():
    p = argparse.ArgumentParser(
        prog="chainlog", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Bookmark, find and resume Claude Code / Codex sessions for QA chain testing.",
        epilog="examples:\n  chainlog save \"r2: refund 500\" -s fail     right after /exit\n"
               "  chainlog 4321                               find sessions for PR 4321\n"
               "  chainlog next 4321                          resume the newest round\n\n" + REF_HELP)
    p.add_argument("--version", action="version", version="chainlog %s" % __version__)
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    s = sub.add_parser("save", help="bookmark a session with a note (the one you just exited, or this one)",
                       epilog=REF_HELP)
    s.add_argument("note", nargs="*", help="the note (quote it, or use --args-stdin)")
    s.add_argument("-s", "--status", help="wip | pass | fail | blocked")
    s.add_argument("-c", "--chain", help="chain: 4321, shop#4321, owner/repo#4321 or a PR URL (default: from the PR link)")
    s.add_argument("--no-chain", action="store_true", help="do not link this save to a chain")
    s.add_argument("-t", "--tag", action="append", help="add a tag (repeatable)")
    s.add_argument("--id", metavar="REF", help="which session (default: the one you just exited here)")
    s.add_argument("--pick", action="store_true", help="choose from recent sessions")
    s.add_argument("--url", help="attach a URL (e.g. a claude.ai/code session or the PR)")
    s.add_argument("--title", help="set your own title")
    s.add_argument("--args-stdin", action="store_true", help="read note and -s/-c/-t flags from stdin (skills)")
    s.add_argument("--via", choices=["cli", "skill:claude", "skill:codex"], help=argparse.SUPPRESS)
    s.add_argument("--json", action="store_true", help="machine-readable output")

    l = sub.add_parser("ls", help="list sessions (newest first)")
    l.add_argument("--chain", metavar="KEY")
    l.add_argument("--pr", metavar="N", help="sessions for a PR number (or repo#N)")
    l.add_argument("--here", action="store_true", help="this folder / repository only")
    l.add_argument("--since", metavar="AGE", help="e.g. 7d, 12h, 90m")
    l.add_argument("--harness", choices=["claude", "codex"])
    l.add_argument("--via", choices=["terminal", "vscode", "desktop"], help="where the session was started")
    l.add_argument("--unsaved", action="store_true")
    l.add_argument("--saved", action="store_true")
    l.add_argument("--starred", action="store_true", help="only starred sessions")
    l.add_argument("--kept", action="store_true", help="only sessions you keep a copy of")
    l.add_argument("--all", action="store_true", help="include sessions without prompts")
    l.add_argument("-n", type=_positive, default=30, help="max rows (default 30)")
    l.add_argument("--json", action="store_true", help="machine-readable output")

    f = sub.add_parser("find", help="search titles, notes, chains, PRs, branches, previews")
    f.add_argument("text", nargs="+")
    f.add_argument("-n", type=_positive, default=20, help="max rows (default 20)")
    f.add_argument("--json", action="store_true", help="machine-readable output")

    sh = sub.add_parser("show", help="details + exact resume command", epilog=REF_HELP)
    sh.add_argument("ref", metavar="REF")
    sh.add_argument("--copy", action="store_true", help="copy the resume command to the clipboard")
    sh.add_argument("--json", action="store_true", help="machine-readable output")

    r = sub.add_parser("resume", help="resume a session in its folder (alias: r)", epilog=REF_HELP)
    r.add_argument("ref", metavar="REF")
    r.add_argument("--fork", action="store_true", help="branch off instead of continuing")
    r.add_argument("--print", action="store_true", help="only print the command")
    r.add_argument("--here", action="store_true", help="run from this folder instead")

    c = sub.add_parser("chain", help="list chains, or one chain's rounds")
    c.add_argument("key", nargs="?", metavar="KEY", help="shop#4321, 4321, or a session REF")
    c.add_argument("--json", action="store_true", help="machine-readable output")

    n = sub.add_parser("next", help="resume the newest round of a chain")
    n.add_argument("key", metavar="KEY", help="shop#4321 or 4321 (4321/2 for a given round)")
    n.add_argument("--fork", action="store_true", help="branch off instead of continuing")
    n.add_argument("--print", action="store_true", help="only print the command")
    n.add_argument("--here", action="store_true", help="run from this folder instead")

    no = sub.add_parser("note", help="add a note to a session", epilog=REF_HELP)
    no.add_argument("ref", metavar="REF")
    no.add_argument("text", nargs="+")
    stt = sub.add_parser("status", help="set status: wip | pass | fail | blocked", epilog=REF_HELP)
    stt.add_argument("ref", metavar="REF")
    stt.add_argument("value", metavar="STATUS")
    tg = sub.add_parser("tag", help="tag a session: chainlog tag REF +add -remove", epilog=REF_HELP)
    tg.add_argument("ref", metavar="REF")
    tg.add_argument("tags", nargs="*", metavar="+TAG/-TAG")
    sr = sub.add_parser("star", help="star a session (a favourite); see them with ls --starred", epilog=REF_HELP)
    sr.add_argument("refs", nargs="+", metavar="REF")
    us = sub.add_parser("unstar", help="take a session off your starred list", epilog=REF_HELP)
    us.add_argument("refs", nargs="+", metavar="REF")
    kp = sub.add_parser("keep", help="keep a copy of a session so Claude's cleanup cannot delete it", epilog=REF_HELP,
                        description="Claude Code deletes transcripts after cleanupPeriodDays (default 30). A kept session is "
                                    "copied (compressed) into chainlog's folder, and resuming it puts the copy back if "
                                    "Claude deleted the original. Saving or starring a session keeps it automatically "
                                    "(chainlog config set keep.auto false turns that off).")
    kp.add_argument("refs", nargs="*", metavar="REF")
    kp.add_argument("--refresh", action="store_true", help="bring the copies you have up to date (a session you kept may have grown)")
    kp.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    uk = sub.add_parser("unkeep", help="delete the kept copy of a session (the original is not touched)", epilog=REF_HELP)
    uk.add_argument("refs", nargs="+", metavar="REF")
    uk.add_argument("--yes", action="store_true", help="do not ask, even when the copy is all that is left")
    rs = sub.add_parser("restore", help="put a kept copy back where Claude looks for it (resume does this itself)", epilog=REF_HELP)
    rs.add_argument("ref", metavar="REF")
    dl = sub.add_parser("delete", help="delete sessions from Claude, from chainlog, or both", epilog=REF_HELP,
                        description="Deletes Claude's transcript for a session (and the folder of sub-agent output next to it) "
                                    "and/or chainlog's side: the kept copy, and the session is hidden from every list "
                                    "(chainlog undo brings the listing back). A running session is refused. Cannot be undone "
                                    "for the files.")
    dl.add_argument("refs", nargs="+", metavar="REF")
    dl.add_argument("--from", dest="where", choices=delete_mod.WHERE, default="both",
                    help="both (default), claude (keep chainlog's copy), or chainlog (leave Claude's file)")
    dl.add_argument("--yes", action="store_true", help="do not ask")
    rn = sub.add_parser("retention", help="show or set how long Claude keeps transcripts (cleanupPeriodDays)",
                        description="With no argument: how long Claude keeps a session after you last used it, and what expires "
                                    "soon. With DAYS (1-%d): sets cleanupPeriodDays in ~/.claude/settings.json after showing "
                                    "the change; --default removes it (Claude then uses 30)." % retention_mod.MAXIMUM)
    rn.add_argument("days", nargs="?", type=_positive, metavar="DAYS")
    rn.add_argument("--default", action="store_true", help="remove the setting (Claude's own default is 30 days)")
    rn.add_argument("--yes", action="store_true", help="do not ask")
    rn.add_argument("--json", action="store_true", help="with DAYS: print what the change would do, and write nothing")
    fg = sub.add_parser("forget", help="hide a session from lists (undo restores it)", epilog=REF_HELP)
    fg.add_argument("ref", metavar="REF")
    sub.add_parser("undo", help="revert your last save / note / status / tag / star / forget")

    rv = sub.add_parser("review", help="go through unsaved sessions one by one",
                        description="For each unsaved session: s = save with a note, a = save with a summary, "
                                    "r = reopen it, f = hide it from lists (nothing is deleted), n = skip, q = stop. "
                                    "Use --auto to save them all at once.")
    rv.add_argument("--all", action="store_true", help="include sessions from before install / older than lookback")
    rv.add_argument("--json", action="store_true", help="machine-readable output")
    rv.add_argument("--auto", action="store_true",
                    help="save every unsaved session with an automatic summary (asks first; needs --yes without a terminal)")
    rv.add_argument("--yes", action="store_true", help="with --auto: do not ask")

    cf = sub.add_parser("config", help="show or change settings, e.g. config set unsaved.action remind")
    cf.add_argument("action", nargs="?", choices=["list", "get", "set"])
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")
    cf.add_argument("--json", action="store_true", help="machine-readable output")

    su = sub.add_parser("setup", help="install integrations (shows every change and asks first)")
    su.add_argument("components", nargs="*", metavar="PART",
                    help="all | cli | claude | codex | shell | policy | scheduler | menubar (default: all, which leaves out scheduler and menubar)")
    su.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    su.add_argument("--yes", action="store_true", help="apply without asking (agents: only after the user agreed)")
    su.add_argument("--skip", action="append", default=[], metavar="PART")
    su.add_argument("--policy", choices=list(config_mod.ACTIONS), help="unsaved-session policy (see README)")
    su.add_argument("--engine", choices=list(config_mod.ENGINES), help="summary engine for --policy summarize")
    su.add_argument("--local", action="store_true", help="add this checkout as the Claude marketplace (development)")
    su.add_argument("--json", action="store_true", help="machine-readable output")

    un = sub.add_parser("uninstall", help="remove integrations (keeps your data unless --purge)")
    un.add_argument("--purge", action="store_true", help="also delete all chainlog data")
    un.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    dr = sub.add_parser("doctor", help="health checks")
    dr.add_argument("--json", action="store_true", help="machine-readable output")
    sc = sub.add_parser("scan", help="re-index transcripts")
    sc.add_argument("--full", action="store_true", help="re-read every transcript")

    sw = sub.add_parser("_sweep")
    sw.add_argument("--tty", action="store_true")
    sw.add_argument("--cron", action="store_true")
    sw.add_argument("--background", action="store_true")
    sw.add_argument("--shell-pid", type=int)
    nd = sub.add_parser("_nudge")
    nd.add_argument("--shell-pid", type=int, required=True)
    mn = sub.add_parser("_menu")
    mn.add_argument("--find", metavar="TEXT", help="instead of the lists: search every session for TEXT")
    mn.add_argument("--starred", action="store_true", help="with --find: only among starred sessions")
    mn.add_argument("--every-word", action="store_true", help="with --find: only sessions that contain every word")
    ak = sub.add_parser("ask", help="ask Claude or Codex about your sessions",
                        description="Ask Claude or Codex about the sessions you worked on, or search them with details. It uses "
                                    "the `claude` / `codex` you are already signed in to. Connect once with --connect; "
                                    "with no question in a terminal it starts a small chat.")
    ak.add_argument("question", nargs="*", help="what to ask, e.g.: which sessions touched the refund PR, and how did they end?")
    ak.add_argument("--connect", choices=list(config_mod.ASK_ENGINES), metavar="claude|codex",
                    help="agree to send session text to that tool, and use it")
    ak.add_argument("--disconnect", action="store_true", help="stop sending session text to any tool")
    ak.add_argument("--status", action="store_true", help="what is installed, signed in and connected")
    ak.add_argument("-e", "--engine", choices=list(config_mod.ASK_ENGINES), help="use this tool for this question")
    ak.add_argument("-y", "--yes", action="store_true", help="with --connect: agree without asking")
    ak.add_argument("--json", action="store_true")
    sub.add_parser("_ask")
    cx = sub.add_parser("context", help="merge what many sessions taught you into one context a new session can use",
                        description="A context collects what several sessions taught you (say, all your cloud backup work) into "
                                    "one small file. A new session starts with a few lines that point to it, and reads it only "
                                    "when its task needs it. With no subcommand: list your contexts.")
    cxs = cx.add_subparsers(dest="cx", metavar="ACTION")
    c_new = cxs.add_parser("new", help="make a context from sessions, and build it")
    c_new.add_argument("name")
    c_new.add_argument("refs", nargs="*", metavar="ID", help="sessions to put in it (ids from chainlog find / ls)")
    c_new.add_argument("--find", nargs="+", metavar="WORDS", help="also every session a search for these words finds")
    c_new.add_argument("--chain", metavar="KEY", help="also every round of this chain, and new rounds later")
    c_new.add_argument("-q", "--question", action="append", help="a question to ask each session (repeat; default: your ten)")
    c_new.add_argument("--no-build", action="store_true", help="only save it; build later with update")
    c_upd = cxs.add_parser("update", help="read new and changed sessions, and merge again")
    c_upd.add_argument("name")
    c_add = cxs.add_parser("add", help="add sessions (read on the next update)")
    c_add.add_argument("name")
    c_add.add_argument("refs", nargs="*", metavar="ID")
    c_add.add_argument("--find", nargs="+", metavar="WORDS")
    c_rem = cxs.add_parser("remove", help="take sessions out (on the next update)")
    c_rem.add_argument("name")
    c_rem.add_argument("refs", nargs="+", metavar="ID")
    c_use = cxs.add_parser("use", help="start a new Claude or Codex session with the context attached")
    c_use.add_argument("name")
    c_use.add_argument("task", nargs="*", help="what to do (optional): it goes into the first message")
    c_use.add_argument("--tool", choices=list(config_mod.ASK_ENGINES), help="claude or codex (default: the newest session's)")
    c_use.add_argument("--folder", metavar="DIR", help="where to start (default: the newest session's folder)")
    c_use.add_argument("--print", action="store_true", help="only print the command")
    c_ref = cxs.add_parser("ref", help="print the lines that attach the context, to paste into any session")
    c_ref.add_argument("name")
    c_ref.add_argument("--copy", action="store_true", help="also copy them to the clipboard")
    c_show = cxs.add_parser("show", help="what is in a context")
    c_show.add_argument("name")
    c_rm = cxs.add_parser("rm", help="delete a context (its notes; never your sessions)")
    c_rm.add_argument("name")
    c_q = cxs.add_parser("questions", help="the questions new contexts ask each session")
    c_q.add_argument("--set", nargs="+", metavar="QUESTION", help="save these as your default questions")
    c_q.add_argument("--reset", action="store_true", help="back to chainlog's ten")
    cxs.add_parser("ls", help="list contexts")
    for c in (cx, c_new, c_upd, c_add, c_rem, c_use, c_ref, c_show, c_rm, c_q):
        c.add_argument("--json", action="store_true", help=argparse.SUPPRESS if c is not cx else "machine-readable output")
    for c in (cx, c_new, c_upd):
        c.add_argument("-e", "--engine", choices=list(config_mod.ASK_ENGINES), help="build with claude or codex")
    for c in (cx, c_new, c_upd, c_rm):
        c.add_argument("-y", "--yes", action="store_true", help="agree / confirm without asking")
    sub.add_parser("_context")
    sub.add_parser("help")
    # hidden commands must not show in `chainlog --help`
    if hasattr(sub, "_choices_actions"):  # argparse internals; if they ever change, the hidden commands just show in --help
        sub._choices_actions = [a for a in sub._choices_actions if a.dest not in HIDDEN]
    return p


DISPATCH = {
    "save": cmd_save, "ls": cmd_ls, "find": cmd_find, "show": cmd_show, "resume": cmd_resume,
    "chain": cmd_chain, "next": cmd_next, "note": cmd_note, "status": cmd_status, "tag": cmd_tag,
    "star": cmd_star, "unstar": cmd_unstar, "keep": cmd_keep, "unkeep": cmd_unkeep, "restore": cmd_restore,
    "delete": cmd_delete, "retention": cmd_retention, "forget": cmd_forget, "undo": cmd_undo, "review": cmd_review, "config": cmd_config,
    "setup": cmd_setup, "uninstall": cmd_uninstall, "doctor": cmd_doctor, "scan": cmd_scan,
    "_sweep": cmd_sweep, "_nudge": cmd_nudge, "_menu": cmd_menu, "ask": cmd_ask, "_ask": cmd_ask_stream,
    "context": cmd_context, "_context": cmd_context_stream,
}


def _parse(parser, argv):
    if argv and argv[0] in ALIASES:
        argv = [ALIASES[argv[0]]] + argv[1:]
    if argv and argv[0] not in VISIBLE + HIDDEN and not argv[0].startswith("-"):
        if len(argv) == 1 and re.search(r"#?\d+/\d+$", argv[0]):
            argv = ["show"] + argv  # `chainlog 4321/2`: that round of the chain
        else:
            argv = ["find"] + argv  # `chainlog 4321` == `chainlog find 4321`
    if argv and argv[0] == "tag":
        # `tag REF +x -y`: '-y' removes a tag, so take the words verbatim instead of as options
        if len(argv) >= 2 and argv[1] in ("-h", "--help"):
            return parser.parse_args(argv)
        return argparse.Namespace(cmd="tag", ref=argv[1] if len(argv) > 1 else "",
                                  tags=[t for t in argv[2:] if t != "--"])
    return parser.parse_args(argv)


def main(argv=None, rt=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    rt = rt or Runtime()
    parser = build_parser()
    try:
        args = _parse(parser, argv)
        if args.cmd == "help":
            parser.print_help()
            return 0
        if not args.cmd:
            args.n = 15
            return cmd_default(rt, args)
        if args.cmd == "tag" and not args.ref:
            raise Exit(1, "usage: chainlog tag REF +add -remove")
        return DISPATCH[args.cmd](rt, args)
    except Exit as e:
        if e.message:
            _err(e.message)
        return e.code
    except NotSaved as e:
        _err(str(e))
        return 1
    except ValueError as e:  # bad user input (durations, PR numbers, …): a message, not a traceback
        if rt.env.get("CHAINLOG_DEBUG"):
            raise
        _err(str(e))
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # output piped into `head`: stop quietly (and keep Python from complaining at exit)
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return 0
