"""Unsaved-session policy engine (plan §3.7).

Finds sessions that ended at least `unsaved.after` ago, are unsaved, were not handled before and
ended after install, then applies the user's policy once per session:
  summarize – auto-save with a summary (◐)
  list      – show them (with last-work previews) at the next prompt / in `chainlog review`
  remind    – desktop + herdr notification (+ a terminal line when run from a prompt)
Triggered by the launchd/systemd timer (mode=cron), the zsh prompt hook (mode=tty) or any
chainlog command (mode=cmd). Only one sweep runs at a time (sweep.lock); it re-reads everything
after taking the lock, so two triggers can never act on the same session twice. AI summaries never
run in the foreground of a prompt or a command: those modes hand them to a detached background run.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from contextlib import contextmanager

from chainlog import chain as chain_mod
from chainlog import ui
from chainlog.paths import ensure_private_dir
from chainlog.store import spool
from chainlog.store.events import new_event
from chainlog.summarize import summarize

INFERRED_MIN = 30 * 60  # no end hook: only treat as ended after this much inactivity


@contextmanager
def sweep_lock(rt):
    """Non-blocking: yields False when another sweep is already running."""
    try:
        home = ensure_private_dir(rt.data_home)
        fd = os.open(str(home / "sweep.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        yield False
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


RESUMED_GRACE = 60  # transcript activity this long after the end hook = the session was resumed


def _ended_at(s):
    """(ended_at, exact). Exact when an end hook fired and nothing happened after it; a session that
    was resumed afterwards (activity after the end) is only "ended" again after inactivity."""
    end = s["times"].get("ended_at")
    active = s["times"].get("last_activity_at")
    reopened = end and any(h["type"] == "hook.start" and h["at"] > end for h in s.get("hooks") or [])
    if end and not reopened and not (active and active > end + RESUMED_GRACE):
        return end, True
    return active, False


def eligible(s, cfg):
    """Due time for this session's current "ending", or None. Acts once per ending: a session that
    was resumed and ended again becomes eligible again (an auto-summary is then refreshed)."""
    u = cfg["unsaved"]
    installed = cfg.get("installed_at") or 0
    if s.get("child") or s.get("hidden") or not s.get("interactive", True) or not s.get("has_prompt"):
        return None
    if s["saved"] == "user" or s.get("live"):
        return None
    ended, exact = _ended_at(s)
    if not ended or ended < installed:
        return None
    if s["saved"] == "auto" and (s.get("last_save_at") or 0) >= ended:
        return None
    handled = s.get("unsaved_handled")
    if handled and (handled.get("at") or 0) >= ended and handled.get("action") == u["action"]:
        return None  # after a policy change, sessions handled under the old policy get the new one
    wait = u["after"] if exact else max(u["after"], INFERRED_MIN)
    return ended + wait


def due(index, now):
    cfg = index.cfg
    out, upcoming = [], []
    for s in index.sessions.values():
        d = eligible(s, cfg)
        if d is None:
            continue
        ended, _ = _ended_at(s)
        if now - ended > cfg["unsaved"]["lookback"]:
            continue
        (out if now >= d else upcoming).append((d, s))
    out.sort(key=lambda x: x[0])
    return out, (min(d for d, _s in upcoming) if upcoming else None)


def _handled(rt, s, action, result, due_at):
    return new_event(rt, "unsaved.handled", s["ref"], "sweep",
                     {"action": action, "result": result, "due_at": int(due_at or 0)})


def run(index, mode="cmd", write=None):
    """Returns printable lines (only shown when mode == 'tty')."""
    from chainlog.index import load, write_events
    rt, cfg = index.rt, index.cfg
    write = write or (lambda evs: write_events(rt, evs))
    due_now, next_due = due(index, rt.now())
    if not due_now:  # the common case: nothing to do, no lock, no reload
        spool.set_due_min(rt, next_due if cfg["unsaved"]["action"] != "off" else None, since=rt.now())
        return []
    with sweep_lock(rt) as mine:
        if not mine:
            return []  # another sweep is acting right now
        started = rt.now()
        index = load(rt, cfg)  # re-read under the lock: the other sweep may just have finished
        return _run_locked(index, mode, write, started)


def _run_locked(index, mode, write, started=None):
    rt, cfg = index.rt, index.cfg
    set_due = lambda value: spool.set_due_min(rt, value, since=started)
    action = cfg["unsaved"]["action"]
    now = rt.now()
    due_now, next_due = due(index, now)
    sessions = [s for _d, s in due_now]
    lines = []
    if action == "off" or not sessions:
        set_due(next_due if action != "off" else None)
        return lines

    if action == "summarize":
        engine = cfg["summary"]["engine"]
        if mode in ("tty", "cmd") and engine != "local":
            _spawn_background(rt)
            if mode == "tty":
                lines.append("chainlog ▸ auto-saving %d unsaved session%s (summary in background)" % (
                    len(sessions), "" if len(sessions) == 1 else "s"))
            set_due(now + 300)  # the background run rewrites it
            return lines
        for s in sessions:
            text, used, why = summarize(rt, s, cfg)
            data = {"summary": text, "summary_engine": used}
            if why:
                data["fallback_reason"] = why
            if cfg.get("auto_chain", True) and not s["chains"]:
                hinted = next((h["chain"] for h in reversed(s["hooks"]) if h.get("chain")), None)
                key, src = (chain_mod.key_from_arg(hinted, s, index.chains), "CHAINLOG_CHAIN") if hinted \
                    else chain_mod.suggest_key(s)
                if key:
                    data["chain"], data["chain_source"] = key, src
            # one session at a time: a slow AI summary never holds back the others' results
            write([new_event(rt, "save", s["ref"], "auto", data),
                   _handled(rt, s, "summarize", "fallback" if why else "ok", s["times"].get("ended_at"))])
            lines.append('chainlog ▸ auto-saved: %s %s "%s"%s' % (
                s["harness"], s["short"], ui.fit(s["title"]["value"], 60),
                " → %s" % data["chain"] if data.get("chain") else ""))
    elif action == "list":
        if mode == "tty":
            write([_handled(rt, s, "list", "shown", s["times"].get("ended_at")) for s in sessions])
            lines.append("chainlog ▸ %d unsaved session%s — chainlog review" % (
                len(sessions), "" if len(sessions) == 1 else "s"))
            for s in sessions[:3]:
                lines.append('  %s %s "%s"' % (s["harness"], s["short"], ui.fit(s["title"]["value"], 70)))
                if s.get("last_work"):
                    lines.append('     last work: "%s"' % ui.fit(ui.safe(s["last_work"]), 100))
            if len(sessions) > 3:
                lines.append("  … and %d more — chainlog review" % (len(sessions) - 3))
        else:
            # nothing visible to do from cron or a plain command (the default view shows them):
            # keep the due time "now" so the next prompt shows them
            set_due(min(d for d, _s in due_now))
            return lines
    elif action == "remind":
        if mode == "cmd":
            set_due(min(d for d, _s in due_now))  # a plain command shows them in its Unsaved block
            return lines
        if mode == "tty":
            # the user is at a prompt: the terminal line is the reminder (no slow desktop calls here)
            write([_handled(rt, s, "remind", "terminal", s["times"].get("ended_at")) for s in sessions])
            for s in sessions:
                lines.append('chainlog ▸ unsaved: %s %s "%s" — chainlog save --id %s "…"' % (
                    s["harness"], s["short"], ui.fit(s["title"]["value"], 60), s["short"]))
        else:
            from chainlog.notify import remind
            missed = []
            if len(sessions) > 3:  # one notification for a batch, not a flood
                used = remind(rt, "chainlog: %d unsaved sessions" % len(sessions), "run: chainlog review")
                if used:
                    write([_handled(rt, s, "remind", ",".join(used), s["times"].get("ended_at")) for s in sessions])
                    set_due(next_due)
                    return lines
                set_due(now)
                return lines
            for s in sessions:
                body = '"%s" · run: chainlog save --id %s' % (s["title"]["value"][:60], s["short"])
                used = remind(rt, "chainlog: unsaved session", body)
                if used:
                    write([_handled(rt, s, "remind", ",".join(used), s["times"].get("ended_at"))])
                else:
                    missed.append(s)  # nobody was told: leave it for the next prompt
            if missed:
                set_due(now)
                return lines
    set_due(next_due)
    return lines


def _spawn_background(rt):
    """Detached `chainlog _sweep --background` so no prompt or command waits for an AI summary."""
    launcher = rt.env.get("CHAINLOG_LAUNCHER") or "chainlog"
    try:
        subprocess.Popen([launcher, "_sweep", "--background"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                         env=rt.env, cwd=os.path.expanduser("~"), close_fds=True)
    except OSError as e:
        sys.stderr.write("chainlog: could not start background summary: %s\n" % e)
