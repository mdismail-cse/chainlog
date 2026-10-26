"""`chainlog review`: triage unsaved sessions one by one, with last-ask / last-work previews."""
from __future__ import annotations

from chainlog import ui
from chainlog.store.events import new_event
from chainlog.summarize import summarize

KEYS = "[s] save with a note  [a] save with a summary  [r] reopen  [f] hide (nothing is deleted)  [n] skip  [q] stop"


def unsaved(index, include_all=False):
    cfg = index.cfg
    now = index.rt.now()
    installed = cfg.get("installed_at") or 0
    out = []
    for s in index.visible():
        if s["saved"] != "none" or s.get("live") or not s.get("interactive", True) or not s.get("has_prompt"):
            continue
        if s["harness"] == "codex" and not s["times"].get("ended_at") \
                and now - (s["times"].get("last_activity_at") or 0) < 300:
            continue  # a Codex session written to in the last 5 min is most likely still running
        ended = s["times"].get("ended_at") or s["times"].get("last_activity_at") or 0
        if not include_all and (ended < installed or now - ended > cfg["unsaved"]["lookback"]):
            continue
        out.append(s)
    return out


def build_auto_save(rt, cfg, s, chains=None):
    """The `save` event for saving one session with an automatic summary, and its PR chain when it names one
    (a CHAINLOG_CHAIN hint from the hook wins). Nothing is written. Returns
    (event, summary text, engine used, why it fell back or None, chain key or None, a key_conflict() or None)."""
    from chainlog import chain as chain_mod
    text, used, why = summarize(rt, s, cfg)
    data = {"summary": text, "summary_engine": used}
    if why:
        data["fallback_reason"] = why
    conflict = None
    if not s["chains"] and cfg.get("auto_chain", True):
        hinted = next((h["chain"] for h in reversed(s["hooks"]) if h.get("chain")), None)
        key, src = (chain_mod.key_from_arg(hinted, s, chains or {}), "CHAINLOG_CHAIN") if hinted else chain_mod.suggest_key(s)
        if key:
            data["chain"], data["chain_source"] = key, src
        else:
            conflict = chain_mod.key_conflict(s)
    return new_event(rt, "save", s["ref"], "auto", data), text, used, why, data.get("chain"), conflict


def auto_save(rt, cfg, s, write, chains=None):
    """Build and write it. Returns (summary text, engine used, the chain key or None, a key_conflict() or None)."""
    event, text, used, _why, chain, conflict = build_auto_save(rt, cfg, s, chains)
    write([event])
    return text, used, chain, conflict


def interactive(index, sessions, st, ask, write, out, resume_cb, save_cb):
    rt = index.rt
    now = rt.now()
    total = len(sessions)
    out("%d unsaved session%s%s\n" % (total, "" if total == 1 else "s",
                                      "" if total else " — nothing to review"))
    for i, s in enumerate(sessions, 1):
        ended = s["times"].get("ended_at") or s["times"].get("last_activity_at")
        branch = (s.get("git") or {}).get("branch") or ""
        meta = " · ".join(x for x in (ui.home(rt, s.get("cwd") or ""), branch,
                                      "ended %s" % ui.ago_phrase(ended, now) if ended else "",
                                      ui.duration(s["times"].get("started_at"), ended)) if x)
        out(" %s  %s %s  %s" % (st.bold(str(i)), s["harness"], s["short"], st.dim(meta)))
        out('    "%s"' % s["title"]["value"])
        for line in ui.preview_lines(st, s):
            out(line)
        while True:
            choice = (ask("    %s › " % st.dim(KEYS)) or "").strip().lower()[:1]
            if choice == "s":
                note = ask("    note: ")
                if note is None:
                    return
                out("    " + save_cb(s, note))
                break
            if choice == "a":
                text, used, _chain, _conflict = auto_save(rt, index.cfg, s, write, index.chains)
                out("    ◐ auto-saved: %s%s" % (text, st.dim(" (%s)" % used)))
                break
            if choice == "r":
                resume_cb(s)
                return
            if choice == "f":
                write([new_event(rt, "forget", s["ref"], "cli")])
                out("    forgotten (chainlog undo to restore)")
                break
            if choice in ("n", ""):
                break
            if choice == "q":
                return
            out("    " + st.dim("keys: s a r f n q"))
        out("")
