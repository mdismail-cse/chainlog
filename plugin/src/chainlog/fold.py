"""fold(facts, events, live) -> sessions, chains. A pure function, run on every call.

Rules (SCHEMA.md "Fold"): events sorted by (ts, log position); scalars last-writer-wins; notes append; tags
are a set; chain links replay in order; `undo` cancels the event it names; unknown types ignored.
"""
from __future__ import annotations

import os
import time

from chainlog.chain import build_chains
from chainlog.ui import safe

TITLE_ORDER = ("user", "custom", "agent", "ai", "thread", "hook", "herdr")


def _blank(ref):
    harness, _, native = ref.partition(":")
    return {
        "ref": ref, "harness": harness, "native_id": native, "short": native[:8],
        "titles": {}, "saved": "none", "notes": [], "status": None, "tags": [], "chains": [],
        "cwd": None, "git": {}, "prs": [], "urls": [],
        "times": {"started_at": None, "last_activity_at": None, "ended_at": None},
        "end": {"reason": None, "via": "none"}, "lineage": {}, "origin": {}, "hooks": [],
        "transcript": {"path": None, "present": False, "archived": False},
        "last_ask": "", "last_work": "", "first_ask": "", "stats": None,
        "harness_version": None, "entrypoint": None, "interactive": True, "has_prompt": False,
        "live": False, "hidden": False, "summary": None, "unsaved_handled": None, "warnings": [],
        "child": False, "starred": False, "kept": False,
    }


def _from_facts(f, codex_titles):
    s = _blank("%s:%s" % (f["harness"], f["id"]))
    s["titles"] = {k: v for k, v in (f.get("titles") or {}).items() if isinstance(v, str) and v.strip()}
    if f["harness"] == "codex" and codex_titles.get(f["id"]):
        s["titles"]["thread"] = codex_titles[f["id"]]
    s["cwd"] = f.get("cwd")
    s["git"] = {k: v for k, v in (("branch", f.get("branch")), ("remote", f.get("remote"))) if v}
    s["prs"] = list(f.get("prs") or [])
    s["times"]["started_at"] = f.get("started_at")
    s["times"]["last_activity_at"] = max(x for x in (f.get("last_ts"), f.get("last_activity"), 0) if x is not None)
    s["transcript"] = {"path": f.get("path"), "present": not f.get("gone"), "archived": bool(f.get("archived"))}
    for k in ("last_ask", "last_work", "first_ask", "stats", "interactive", "has_prompt", "child"):
        if f.get(k) is not None:
            s[k] = f[k]
    s["harness_version"] = f.get("version")
    s["entrypoint"] = f.get("entrypoint")
    if f.get("forked_from"):
        s["lineage"]["forked_from"] = f["forked_from"]
    if f.get("parent"):
        s["lineage"]["parent"] = f["parent"]
    if f.get("remote_control"):
        s["remote_control"] = True
    s["warnings"] = list(f.get("warnings") or [])
    return s


def _title(s):
    for src in TITLE_ORDER:
        v = safe(s["titles"].get(src) if isinstance(s["titles"].get(src), str) else "")
        if v:
            return {"value": v, "source": src}
    if s.get("first_ask"):
        return {"value": safe(s["first_ask"]), "source": "first-ask"}
    snap = s.get("snap") or {}
    if snap.get("title"):
        return {"value": safe(snap["title"]), "source": "saved"}
    base = os.path.basename((s.get("cwd") or "").rstrip("/")) or s["harness"]
    when = s["times"].get("started_at") or s["times"].get("last_activity_at")
    day = time.strftime("%b %d", time.localtime(when)) if when else ""
    return {"value": ("%s %s" % (base, day)).strip(), "source": "cwd"}


def fold(facts_list, events, live=(), codex_titles=None):
    codex_titles = codex_titles or {}
    sessions = {}
    # a present transcript always wins over a remembered ("gone") copy of the same session, e.g. a
    # Codex rollout that moved to archived_sessions/
    for f in sorted(facts_list, key=lambda f: bool(f.get("gone"))):
        ref = "%s:%s" % (f.get("harness"), f.get("id"))
        if f.get("gone") and ref in sessions:
            continue
        s = _from_facts(f, codex_titles)
        sessions[s["ref"]] = s

    undone = {e.get("data", {}).get("event") for e in events if e.get("type") == "undo"}
    # by time; events from the same millisecond keep the order they were appended in (random ids
    # must never decide whether "add tag" or "remove tag" happened first)
    for _pos, ev in sorted(enumerate(events), key=lambda x: (x[1].get("ts", 0), x[0])):
        if ev["id"] in undone or ev.get("type") == "undo":
            continue
        try:
            _apply(sessions, ev)
        except (TypeError, AttributeError, ValueError, KeyError):
            continue  # a malformed event is skipped, never fatal
    _finish(sessions, live)
    chains = build_chains(sessions)
    return sessions, chains


def _apply(sessions, ev):
    ref = ev.get("ref")
    if not ref or ":" not in ref:
        return
    s = sessions.get(ref)
    if s is None:
        s = sessions[ref] = _blank(ref)
    d = ev.get("data") or {}
    t = ev["type"]
    at = ev.get("ts", 0) / 1000.0
    if t in ("hook.start", "hook.end"):
        hook = {"type": t, "at": at, "anc": d.get("anc") or [], "tty": d.get("tty") or "",
                "fp": d.get("fp") or {}, "reason": d.get("reason"), "source": d.get("source"),
                "chain": d.get("chain") or ""}
        s["hooks"].append(hook)
        s["origin"].update({k: v for k, v in hook["fp"].items() if v})
        if hook["tty"]:
            s["origin"]["tty"] = hook["tty"]
        if not s["cwd"] and d.get("cwd"):
            s["cwd"] = d["cwd"]
        if d.get("session_title"):
            s["titles"]["hook"] = d["session_title"]
        if t == "hook.start":
            s["times"]["started_at"] = s["times"]["started_at"] or at
        else:
            s["times"]["ended_at"] = at
            s["end"] = {"reason": d.get("reason"), "via": "hook"}
        if not s["transcript"]["path"] and d.get("transcript_path"):
            s["transcript"]["path"] = d["transcript_path"]
    elif t == "save":
        auto = ev.get("src") == "auto"
        if auto:
            if s["saved"] != "user":
                s["saved"] = "auto"
            if d.get("summary"):
                s["summary"] = {"text": d["summary"], "engine": d.get("summary_engine")}
                s["notes"].append({"at": at, "text": d["summary"], "via": "auto", "kind": "summary",
                                   "event": ev["id"]})
        else:
            s["saved"] = "user"
        if d.get("note"):
            s["notes"].append({"at": at, "text": d["note"], "via": ev.get("src"), "kind": "note",
                               "event": ev["id"]})
        if d.get("status"):
            s["status"] = d["status"]
        for tag in d.get("tags") or []:
            if tag not in s["tags"]:
                s["tags"].append(tag)
        if d.get("chain"):
            _link(s, d["chain"], d.get("chain_source") or ("auto" if auto else "save"), d.get("pos"))
        if d.get("title"):
            s["titles"]["user"] = d["title"]
        if d.get("url") and d["url"] not in s["urls"]:
            s["urls"].append(d["url"])
        if isinstance(d.get("snap"), dict):
            s["snap"] = d["snap"]  # what the session looked like when saved (survives transcript loss)
        s["last_save_event"] = ev["id"]
        s["last_save_at"] = at
    elif t == "note":
        s["saved"] = "user"
        s["notes"].append({"at": at, "text": d.get("text", ""), "via": ev.get("src"), "kind": "note",
                           "event": ev["id"]})
    elif t == "status":
        s["status"] = d.get("value")
    elif t == "tag":
        for tag in d.get("add") or []:
            if tag not in s["tags"]:
                s["tags"].append(tag)
        s["tags"] = [x for x in s["tags"] if x not in (d.get("remove") or [])]
    elif t == "chain.link":
        _link(s, d.get("chain"), d.get("source") or "user", d.get("pos"))
    elif t == "chain.unlink":
        s["chains"] = [c for c in s["chains"] if c["id"] != d.get("chain")]
    elif t == "star":
        s["starred"] = True
    elif t == "unstar":
        s["starred"] = False
    elif t == "keep":
        s["kept"] = True
    elif t == "unkeep":
        s["kept"] = False
    elif t == "forget":
        s["hidden"] = True
    elif t == "unforget":
        s["hidden"] = False
    elif t == "unsaved.handled":
        s["unsaved_handled"] = {"action": d.get("action"), "at": at, "result": d.get("result")}


def _finish(sessions, live):
    for lv in live:
        s = sessions.get(lv["ref"])
        if s is not None:
            s["live"] = True
            s["pid"] = lv.get("pid")
            if lv.get("name") and lv.get("name_source") not in (None, "derived"):
                s["titles"].setdefault("registry", lv["name"])

    for s in sessions.values():
        snap = s.get("snap") or {}
        if not s["cwd"] and snap.get("cwd"):
            s["cwd"] = snap["cwd"]
        if not s["times"]["started_at"] and snap.get("started_at"):
            s["times"]["started_at"] = snap["started_at"]
        if snap.get("branch") and not s["git"].get("branch"):
            s["git"]["branch"] = snap["branch"]
        if snap.get("has_prompt"):
            s["has_prompt"] = True
        s["title"] = _title(s)
        if s["times"]["last_activity_at"] is None:
            s["times"]["last_activity_at"] = s["times"]["ended_at"] or s["times"]["started_at"]
    _unique_shorts(sessions)


def _unique_shorts(sessions, minimum=8):
    """Shortest prefix (>= 8 chars) that is unique among all sessions. Codex ids are time-ordered
    UUIDv7, so their first characters are shared by every session from the same hours."""
    ids = sorted(s["native_id"] for s in sessions.values())
    need = {}
    for a, b in zip(ids, ids[1:]):
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        need[a] = max(need.get(a, 0), n + 1)
        need[b] = max(need.get(b, 0), n + 1)
    for s in sessions.values():
        n = max(minimum, need.get(s["native_id"], 0))
        s["short"] = s["native_id"][:n]


def _link(s, chain_id, source, pos=None):
    if not chain_id:
        return
    for c in s["chains"]:
        if c["id"] == chain_id:
            return
    s["chains"].append({"id": chain_id, "pos": pos, "source": source})
