"""`chainlog _menu`: everything the Mac menu bar app shows, as one JSON document (chainlog.menu/1).

The app stays thin: it runs this command, draws the lists, and runs the `resume.command` it is given.
All wording, ordering and "can this be resumed?" decisions are made here, where they are tested.
"""
from __future__ import annotations

from chainlog import __version__
from chainlog import query, resume, review, ui

SCHEMA = "chainlog.menu/1"


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
                   # Claude deleted the original but a kept copy exists: opening puts it back first
                   "restores": bool(p.get("restore")),
                   "blockers": p["blockers"], "notes": p["notes"]},
    }


def build(index, unsaved_limit=8, recent_limit=300, chain_limit=30, round_limit=8, starred_limit=50,
          find=None, find_limit=40, find_starred=False):
    rt = index.rt
    now = rt.now()
    if find is not None:
        # a search: every matching session (not only the listed ones), best match first, and nothing else
        return {"schema": SCHEMA, "chainlog": __version__, "generated_at": now, "query": find,
                "results": [_row(rt, s, now) for s in query.find(index, find, limit=find_limit, starred=find_starred)]}
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
        "unsaved": [_row(rt, s, now) for s in unsaved[:unsaved_limit]],
        "recent": [_row(rt, s, now) for s in recent],
        "starred": [_row(rt, s, now) for s in starred[:starred_limit]],
        "chains": [{"id": c["id"], "rounds": len(c["members"]), "status": c.get("status"),
                    "title": ui.safe(c["title"]), "ago": ui.ago(c["last_activity"], now),
                    "members": [dict(_row(rt, index.sessions[m["ref"]], now, chain_id=c["id"]), round=m["round"])
                                for m in c["members"][-round_limit:]]}
                   for c in chains[:chain_limit]],
    }
