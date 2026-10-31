"""`chainlog _menu`: everything the Mac menu bar app shows, as one JSON document (chainlog.menu/1).

The app stays thin: it runs this command, draws the lists, and runs the `resume.command` it is given.
All wording, ordering and "can this be resumed?" decisions are made here, where they are tested.
"""
from __future__ import annotations

from chainlog import __version__
from chainlog import keep, query, resume, retention, review, ui

SCHEMA = "chainlog.menu/1"
EXPIRING_DAYS = 14  # "expires soon": Claude deletes the original within this many days


def _retention(index):
    """How long Claude keeps transcripts and what is about to go (for the app's gear menu)."""
    rt = index.rt
    days = retention.current(retention.read(rt))
    left = [(s, keep.days_left(rt, s)) for s in index.visible(include_empty=True, include_hidden=True)]
    soon = [s for s, d in left if d is not None and d < EXPIRING_DAYS]
    return {"days": days or retention.DEFAULT, "is_default": days is None, "expiring": len(soon),
            "expiring_unkept": sum(1 for s in soon if not keep.has_copy(rt, s)), "expiring_within": EXPIRING_DAYS}


def _row(rt, s, now, chain_id=None):
    """One session. `chain_id` is the chain it is listed under (a session can be in several); without it,
    the chain it joined last."""
    p = resume.plan(s, rt=rt)
    links = s["chains"]
    link = next((c for c in links if c["id"] == chain_id), None) if chain_id else (links[-1] if links else None)
    # the same moment `chainlog ls` shows: when the session was last worked on (an old end hook can be stale)
    when = s["times"].get("last_activity_at") or s["times"].get("ended_at")
    return {
        "ref": s["ref"], "short": s["short"], "harness": s["harness"],
        "via": resume.origin(s)[1],  # "VS Code" | "Desktop" | None (a plain terminal session carries no tag)
        "title": ui.safe(s["title"]["value"]),
        "saved": s["saved"], "status": s.get("status"), "live": bool(s.get("live")),
        "starred": bool(s.get("starred")), "kept": bool(s.get("kept")),
        "chain": link["id"] if link else None, "round": link.get("round") if link else None,
        "at": when, "ago": ui.ago(when, now),
        "last_work": ui.safe(s.get("last_work")) or None,
        "resume": {"cwd": p["cwd"], "command": resume.shell_command(p),
                   # a session started in the VS Code extension can be reopened in it (macOS); "" otherwise
                   "vscode": resume.vscode_command(s, p) or None,
                   # what to paste somewhere else: the tool's own id for the session, and (VS Code sessions) its vscode:// link
                   "session_id": s["native_id"], "link": resume.vscode_link(s, p) or None,
                   # Claude deleted the original but a kept copy exists: opening puts it back first
                   "restores": bool(p.get("restore")),
                   "blockers": p["blockers"], "notes": p["notes"]},
    }


def _contexts(rt, index):
    """The contexts the Chains tab lists (files on this Mac; nothing is asked of any AI here)."""
    from chainlog import context as ctx_mod
    try:
        return [ctx_mod.summary(rt, index, m) for m in ctx_mod.all_contexts(rt)]
    except Exception:  # a broken context file must not take the whole menu down
        return []


def _context_questions(index):
    """The questions a new context starts with (yours, or chainlog's ten)."""
    from chainlog import context as ctx_mod
    return ctx_mod.questions_default(index.cfg)


def _ask(index):
    """Whether the Ask tab can chat: you chose an engine and agreed to send it session text (config only: it does not
    check that the tool is still installed and signed in, `chainlog ask --status` does)."""
    a = index.cfg.get("ask") or {}
    engine = a.get("engine") or None
    return {"engine": engine, "connected": bool(engine and engine in (a.get("consent") or []))}


def build(index, unsaved_limit=8, recent_limit=300, chain_limit=30, round_limit=8, starred_limit=50,
          find=None, find_limit=40, find_starred=False, find_every_word=False):
    rt = index.rt
    now = rt.now()
    if find is not None:
        # a search: every matching session (not only the listed ones), best match first, and nothing else
        return {"schema": SCHEMA, "chainlog": __version__, "generated_at": now, "query": find,
                "results": [_row(rt, s, now) for s in query.find(index, find, limit=find_limit, starred=find_starred,
                                                                       every_word=find_every_word)]}
    visible = index.visible()
    unsaved = review.unsaved(index)
    shown = {s["ref"] for s in unsaved[:unsaved_limit]}
    recent = [s for s in visible if s["ref"] not in shown][:recent_limit]
    starred = [s for s in visible if s.get("starred")]
    chains = sorted(index.chains.values(), key=lambda c: c["last_activity"], reverse=True)
    return {
        "schema": SCHEMA, "chainlog": __version__, "generated_at": now,
        # `all` is every session chainlog lists; `recent` is the newest `recent_limit` of those not shown as unsaved
        "counts": {"unsaved": len(unsaved), "starred": len(starred), "chains": len(chains), "all": len(visible)},
        # what "unsaved" covers: sessions that ended within this many hours, after chainlog was installed
        "unsaved_window_hours": round(index.cfg["unsaved"]["lookback"] / 3600),
        "retention": _retention(index),
        "keep_auto": bool((index.cfg.get("keep") or {}).get("auto", True)),
        "ask": _ask(index),
        "contexts": _contexts(rt, index),
        "context_questions": _context_questions(index),
        "unsaved": [_row(rt, s, now) for s in unsaved[:unsaved_limit]],
        "recent": [_row(rt, s, now) for s in recent],
        "starred": [_row(rt, s, now) for s in starred[:starred_limit]],
        "chains": [{"id": c["id"], "rounds": len(c["members"]), "status": c.get("status"),
                    "title": ui.safe(c["title"]), "ago": ui.ago(c["last_activity"], now),
                    "members": [dict(_row(rt, index.sessions[m["ref"]], now, chain_id=c["id"]), round=m["round"])
                                for m in c["members"][-round_limit:]]}
                   for c in chains[:chain_limit]],
    }
