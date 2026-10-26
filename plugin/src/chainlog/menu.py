"""`chainlog _menu`: everything the Mac menu bar app shows, as one JSON document (chainlog.menu/1).

The app stays thin: it runs this command, draws the lists, and runs the `resume.command` it is given.
All wording, ordering and "can this be resumed?" decisions are made here, where they are tested.
"""
from __future__ import annotations

from chainlog import __version__
from chainlog import query, resume, review, ui

SCHEMA = "chainlog.menu/1"


def _row(rt, s, now):
    p = resume.plan(s, rt=rt)
    link = s["chains"][-1] if s["chains"] else None
    when = s["times"].get("ended_at") or s["times"].get("last_activity_at")
    return {
        "ref": s["ref"], "short": s["short"], "harness": s["harness"],
        "title": ui.safe(s["title"]["value"]),
        "saved": s["saved"], "status": s.get("status"), "live": bool(s.get("live")),
        "starred": bool(s.get("starred")),
        "chain": link["id"] if link else None, "round": link.get("round") if link else None,
        "at": when, "ago": ui.ago(when, now),
        "last_work": ui.safe(s.get("last_work")) or None,
        "resume": {"cwd": p["cwd"], "command": resume.shell_command(p),
                   "blockers": p["blockers"], "notes": p["notes"]},
    }


def build(index, unsaved_limit=8, recent_limit=8, chain_limit=6, round_limit=8, starred_limit=50,
          find=None, find_limit=40):
    rt = index.rt
    now = rt.now()
    unsaved = review.unsaved(index)
    shown = {s["ref"] for s in unsaved[:unsaved_limit]}
    recent = [s for s in index.visible() if s["ref"] not in shown][:recent_limit]
    chains = sorted(index.chains.values(), key=lambda c: c["last_activity"], reverse=True)[:chain_limit]
    starred = [s for s in index.visible() if s.get("starred")]
    out = {
        "schema": SCHEMA, "chainlog": __version__, "generated_at": now,
        "counts": {"unsaved": len(unsaved), "starred": len(starred)},
        "unsaved": [_row(rt, s, now) for s in unsaved[:unsaved_limit]],
        "recent": [_row(rt, s, now) for s in recent],
        "starred": [_row(rt, s, now) for s in starred[:starred_limit]],
        "chains": [{"id": c["id"], "rounds": len(c["members"]), "status": c.get("status"),
                    "title": ui.safe(c["title"]), "ago": ui.ago(c["last_activity"], now),
                    "members": [dict(_row(rt, index.sessions[m["ref"]], now), round=m["round"])
                                for m in c["members"][-round_limit:]]}
                   for c in chains],
    }
    if find is not None:  # a search across every session, best match first
        out["query"] = find
        out["results"] = [_row(rt, s, now) for s in query.find(index, find, limit=find_limit)]
    return out
