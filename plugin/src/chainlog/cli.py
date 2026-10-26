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
from chainlog import menu as menu_mod
from chainlog import query, resolve, resume, review, sweep, ui
from chainlog.index import NotSaved, load, write_events
from chainlog.noteargs import parse as parse_note_args
from chainlog.redact import redact
from chainlog.runtime import Runtime
from chainlog.store.events import NOTE_MAX, new_event

VISIBLE = ("save", "ls", "find", "show", "resume", "chain", "next", "note", "status", "tag", "star", "unstar",
           "forget", "undo", "review", "config", "setup", "uninstall", "doctor", "scan")
ALIASES = {"list": "ls", "r": "resume", "chains": "chain"}
HIDDEN = ("_sweep", "_nudge", "_menu", "help")
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
    if not c and not no_chain and not chain_arg:
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
                                include_all=args.all, starred=args.starred)
    filtered = any((args.chain, args.pr, args.here, args.since, args.harness, args.unsaved, args.saved, args.starred))
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
    parts = ["started %s" % ui.ago_phrase(t.get("started_at"), now)] if t.get("started_at") else []
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
    if s["harness"] == "claude" and s["transcript"].get("present"):
        from chainlog.sources.claude import retention_days
        left = retention_days(rt) - (now - (t.get("last_activity_at") or now)) / 86400.0
        if left < 7:
            p["notes"].append("Claude deletes this transcript in about %d day%s (cleanupPeriodDays); resume or "
                              "raise cleanupPeriodDays in ~/.claude/settings.json to keep it" % (max(0, left), "" if int(left) == 1 else "s"))
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
    return 0


def cmd_unstar(rt, args):
    return cmd_star(rt, args, on=False)


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
        return _review_auto(rt, cfg, sessions, args)
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


def _review_auto(rt, cfg, sessions, args):
    """`chainlog review --auto`: clear the whole backlog at once with automatic summaries."""
    if args.json:
        raise Exit(1, "--auto and --json can't be combined")
    if not sessions:
        _p("nothing to review — every recent session is saved%s" % (
            "" if args.all else " (chainlog review --all includes older ones)"))
        return 0
    st = ui.Style(rt.color())
    n = len(sessions)
    if rt.isatty() and not args.yes:
        ans = _ask("Save %d unsaved session%s with an automatic summary? You can still add your own note later. [Y/n] "
                   % (n, "" if n == 1 else "s"))
        if ans is None or ans.strip().lower().startswith("n"):
            _p("Cancelled — nothing was saved.")
            return 1
    done = unlinked = 0
    for s in sessions:
        _summary, _engine, chain, conflict = review.auto_save(rt, cfg, s, lambda evs: write_events(rt, evs))
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
    index = load(rt, config_mod.load(rt))
    _p(json.dumps(menu_mod.build(index, find=args.find, find_starred=args.starred), indent=None, separators=(",", ":")))
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
    l.add_argument("--unsaved", action="store_true")
    l.add_argument("--saved", action="store_true")
    l.add_argument("--starred", action="store_true", help="only starred sessions")
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
                    help="save every unsaved session with an automatic summary (asks first)")
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
    sub.add_parser("help")
    # hidden commands must not show in `chainlog --help`
    if hasattr(sub, "_choices_actions"):  # argparse internals; if they ever change, the hidden commands just show in --help
        sub._choices_actions = [a for a in sub._choices_actions if a.dest not in HIDDEN]
    return p


DISPATCH = {
    "save": cmd_save, "ls": cmd_ls, "find": cmd_find, "show": cmd_show, "resume": cmd_resume,
    "chain": cmd_chain, "next": cmd_next, "note": cmd_note, "status": cmd_status, "tag": cmd_tag,
    "star": cmd_star, "unstar": cmd_unstar, "forget": cmd_forget, "undo": cmd_undo, "review": cmd_review, "config": cmd_config,
    "setup": cmd_setup, "uninstall": cmd_uninstall, "doctor": cmd_doctor, "scan": cmd_scan,
    "_sweep": cmd_sweep, "_nudge": cmd_nudge, "_menu": cmd_menu,
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
