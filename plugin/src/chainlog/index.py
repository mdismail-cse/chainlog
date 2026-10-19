"""Load everything a command needs: drain the spool, refresh facts, fold.

Writers (every command that can take the lock) commit spool files to events.jsonl and refresh
factcache.json. When the lock is busy the same work happens in memory only, so reads never block.
"""
from __future__ import annotations

import os

from chainlog import config as config_mod
from chainlog.enrich.git import remote_url
from chainlog.fold import fold
from chainlog.paths import ensure_private_dir
from chainlog.sources import all_sources
from chainlog.sources.base import file_state
from chainlog.store import factcache, spool
from chainlog.store.events import append_events, read_events
from chainlog.store.lock import writer_lock


class Index:
    def __init__(self, rt, cfg, sessions, chains, events, committed):
        self.rt = rt
        self.cfg = cfg
        self.sessions = sessions
        self.chains = chains
        self.events = events
        self.committed = committed

    def visible(self, include_children=False, include_empty=False, include_hidden=False):
        out = []
        for s in self.sessions.values():
            if s.get("child") and not include_children:
                continue
            if s.get("hidden") and not include_hidden:
                continue
            if not include_empty and not s.get("has_prompt") and s["saved"] == "none" and not s["hooks"]:
                continue
            out.append(s)
        out.sort(key=lambda s: s["times"].get("last_activity_at") or 0, reverse=True)
        return out


def refresh_facts(rt, cfg, write=True, keep_refs=(), force=False):
    """Parse new/changed transcripts (all of them with force=True). Facts of transcripts that
    disappeared (Claude deletes them after cleanupPeriodDays) are kept only for sessions in
    `keep_refs` — ones with events (saved, noted, hooked) — so the cache does not grow forever."""
    cache = factcache.load(rt)
    out_cache = {}
    facts = []
    snippets = bool(cfg.get("snippets", True))
    seen = set()
    for src in all_sources():
        for path in src.discover(rt):
            seen.add(path)
            entry = cache.get(path)
            try:
                state = file_state(path, src.parser_version)
            except OSError:
                continue
            if not force and entry and entry.get("state") == state and entry.get("snippets") == snippets:
                f = dict(entry["facts"])
                f.pop("gone", None)  # the file is here (again): a remembered "gone" no longer applies
            else:
                try:
                    f, state = src.parse(path, (entry or {}).get("facts"), rt, snippets=snippets)
                except Exception:  # never let one transcript break every command
                    f = {"harness": src.name, "id": os.path.basename(path).rsplit(".", 1)[0][-36:],
                         "path": path, "titles": {}, "prs": [], "warnings": ["parse error"]}
                if f is None:
                    continue
            out_cache[path] = {"state": state, "facts": f, "snippets": snippets}
            facts.append(f)
    present = {"%s:%s" % (f["harness"], f["id"]) for f in facts}
    # transcripts that disappeared: keep their facts so saved sessions keep titles
    for path, entry in cache.items():
        ef = entry.get("facts") or {}
        ref = "%s:%s" % (ef.get("harness"), ef.get("id"))
        if path not in seen and ef and ref not in present and ref in keep_refs:
            f = dict(ef, gone=True)
            out_cache[path] = {"state": entry.get("state"), "facts": f, "snippets": entry.get("snippets")}
            facts.append(f)
    for f in facts:
        if f["harness"] == "claude" and not f.get("remote") and f.get("cwd"):
            f["remote"] = remote_url(f["cwd"])
    if write and out_cache != cache:
        factcache.save(rt, out_cache)
    return facts


def load(rt, cfg=None, commit=True, force=False):
    cfg = cfg or config_mod.load(rt)
    try:
        ensure_private_dir(rt.data_home)
    except OSError:
        commit = False
    if commit and not cfg.get("installed_at"):
        # the unsaved-session policy only ever acts on sessions that end after this moment
        cfg["installed_at"] = int(rt.now())
        try:
            config_mod.save(rt, cfg)
        except (OSError, ValueError):
            pass  # unwritable, or a broken config.json the user must fix: carry on read-only
    events = read_events(rt)
    known = {e["id"] for e in events}
    committed = False
    with writer_lock(rt, timeout=2.0 if commit else 0.0) as locked:
        new, done = spool.collect(rt, known)
        if locked and commit:
            try:
                append_events(rt, new)
                spool.remove(done)  # only once they are safely in the log
                committed = True
            except OSError:
                pass  # log not writable right now: keep the spool files, work from memory
        events.extend(new)
        keep = {e.get("ref") for e in events}
        facts = refresh_facts(rt, cfg, write=locked and commit, keep_refs=keep, force=force)
    codex_titles = {}
    live = []
    for src in all_sources():
        if src.name == "codex":
            codex_titles = src.titles(rt)
        live.extend(src.live(rt))
    sessions, chains = fold(facts, events, live, codex_titles)
    return Index(rt, cfg, sessions, chains, events, committed)


def write_events(rt, evs):
    """Append CLI events under the lock; fall back to the TMPDIR spool when the data dir is not
    writable (Codex workspace-write sandbox). Returns 'store' or 'fallback'."""
    try:
        with writer_lock(rt, timeout=5.0) as locked:
            if locked:
                append_events(rt, evs)
                return "store"
    except OSError:
        pass
    try:
        for ev in evs:
            spool.write_fallback_event(rt, ev)
    except OSError as e:
        raise NotSaved("could not save: %s is busy or not writable, and the fallback spool is unusable (%s)"
                       % (rt.data_home, e))
    return "fallback"


class NotSaved(RuntimeError):
    pass
