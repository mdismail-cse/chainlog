"""Two jobs:

1. resolve_ref(): turn a REF the user typed (@3, last, 3f9a1c, shop#4321/2, a Codex thread name …)
   into sessions.
2. which(): work out "the session I mean" for a bare `chainlog save` (plan §7). The UX rules live
   in cli.cmd_save: one exact/strong → save; one weak → save + "guessed" + undo hint; several →
   picker with previews; none → picker of recent sessions.

Inside a running session (a skill, or Claude's `!` bash mode) only that harness's live sessions are
candidates: ended-session heuristics never run there, so a note cannot land on an older session.
"""
from __future__ import annotations

import json
import os
import re

from chainlog import chain as chain_mod
from chainlog.enrich import herdr
from chainlog.enrich.git import _git_dir
from chainlog.paths import safe_open
from chainlog.sources import all_sources

WINDOW = 30 * 60
LIVE_WINDOW = 5 * 60  # a Codex rollout written this recently counts as "running"
FP_ORDER = ("herdr_pane", "tmux", "iterm", "term", "tty")  # most specific first


# --- @N refs -----------------------------------------------------------------------------------
def _terminal_key(rt):
    """@N numbers belong to the terminal / pane that printed the list, so a list printed in another
    herdr or tmux pane never changes what @1 means here."""
    fp = _current_fp(rt)
    for k in FP_ORDER:
        if fp.get(k):
            return "%s:%s" % (k, fp[k])
    return "_any"


def _read_lists(rt):
    try:
        with safe_open(rt.data_home / "last-list.json") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {"_any": data if isinstance(data, list) else []}


def save_last_list(rt, refs):
    lists = _read_lists(rt)
    lists[_terminal_key(rt)] = refs
    lists["_any"] = refs
    try:
        from chainlog.paths import atomic_write
        atomic_write(rt.data_home / "last-list.json", json.dumps(lists), rt=rt)
    except OSError:
        pass


def load_last_list(rt):
    lists = _read_lists(rt)
    refs = lists.get(_terminal_key(rt))
    if refs is None:
        refs = lists.get("_any", [])
    return refs if isinstance(refs, list) else []


# --- REF resolution ----------------------------------------------------------------------------
def find_chain(index, text):
    """Chain dict for a chain ref ('shop#4321', '4321', '#4321', free-text key), or None.
    Raises ValueError when a bare number matches several repos."""
    t = (text or "").strip().lower()
    if t in index.chains:
        return index.chains[t]
    norm = chain_mod.normalize_key(text or "")  # PR URLs, owner/repo#N
    if norm in index.chains:
        return index.chains[norm]
    cref = chain_mod.parse_ref(re.sub(r"/\d+$", "", t))  # drop a "/<round>" suffix
    if not cref:
        return None
    repo, num, _rnd = cref
    if repo:
        return index.chains.get(chain_mod.make_key(repo, num))
    hits = [c for c in index.chains.values() if c["id"].endswith("#%d" % num)]
    if len(hits) > 1:
        # inside a checkout, a bare number means this repository's chain
        from chainlog.enrich.git import remote_url, repo_name
        mine = repo_name(remote_url(index.rt.env.get("PWD") or os.getcwd()))
        own = [c for c in hits if mine and c["id"] == chain_mod.make_key(mine, num)]
        if len(own) == 1:
            return own[0]
        raise ValueError("%s matches several chains: %s" % (text, ", ".join(sorted(c["id"] for c in hits))))
    return hits[0] if hits else None


def resolve_ref(index, text):
    """Returns a list of matching sessions (empty = no match, >1 = ambiguous)."""
    rt = index.rt
    text = (text or "").strip()
    sessions = index.sessions
    if not text:
        return []
    if text.startswith("@") and text[1:].isdigit():
        refs = load_last_list(rt)
        n = int(text[1:])
        if 1 <= n <= len(refs) and refs[n - 1] in sessions:
            return [sessions[refs[n - 1]]]
        return []
    if text == "last":
        ended = [s for s in index.visible() if not s.get("live")]
        ended.sort(key=lambda s: s["times"].get("ended_at") or s["times"].get("last_activity_at") or 0,
                   reverse=True)
        return ended[:1]
    if text in sessions:
        return [sessions[text]]
    cref = chain_mod.parse_ref(text)
    if cref:
        try:
            c = find_chain(index, text)
        except ValueError:
            repo, num, rnd = cref
            matches = [c for c in index.chains.values() if c["id"].endswith("#%d" % num)]
            if rnd:  # keep the round the user asked for in every candidate chain
                return [sessions[m["ref"]] for c in matches for m in c["members"] if m["round"] == rnd]
            return [sessions[c["members"][-1]["ref"]] for c in matches]
        if c:
            members = c["members"]
            if cref[2]:
                return [sessions[m["ref"]] for m in members if m["round"] == cref[2]]
            return [sessions[members[-1]["ref"]]]
    free = text.lower()
    if free in index.chains:
        return [sessions[index.chains[free]["members"][-1]["ref"]]]
    harness, _, native = text.partition(":")
    full_id = len(text.split(":")[-1]) >= 32
    pool = [s for s in sessions.values() if full_id or not s.get("child")]
    if native and harness in ("claude", "codex"):
        pool = [s for s in pool if s["harness"] == harness]
    else:
        native = text
    if len(native) >= 4 and all(c in "0123456789abcdef-" for c in native.lower()):
        hits = [s for s in pool if s["native_id"].lower().startswith(native.lower())]
        if hits:
            return hits
    return [s for s in pool if (s["titles"].get("thread") or "").lower() == free
            or s["title"]["value"].lower() == free]


# --- "which session did you mean" ----------------------------------------------------------------
def _cand(s, confidence, reason):
    return {"session": s, "confidence": confidence, "reason": reason}


def sanitize(v):
    """The same character filter spool.sh applies, so both sides compare equal (tmux '%3' -> '3')."""
    return re.sub(r"[^A-Za-z0-9:_./#-]", "", v or "")[:128]


def _current_fp(rt):
    env = rt.env
    raw = {"herdr_pane": env.get("HERDR_PANE_ID"), "tmux": env.get("TMUX_PANE"),
           "iterm": env.get("ITERM_SESSION_ID"), "term": env.get("TERM_SESSION_ID"), "tty": rt.tty_name()}
    return {k: sanitize(v) for k, v in raw.items() if sanitize(v)}


def fp_match(mine, theirs):
    """(best matching key or None, conflict). A key present on both sides with different values is a
    conflict and vetoes the candidate: herdr/tmux panes inherit the tab's TERM_SESSION_ID, so a
    pane mismatch must win over a tab match."""
    best = None
    for k in FP_ORDER:
        a, b = mine.get(k), theirs.get(k)
        if a and b:
            if a != b:
                return None, True
            if best is None:
                best = k
    return best, False


def _fp_ok(mine, theirs):
    key, conflict = fp_match(mine, theirs)
    return bool(key) and not conflict


def _hook_fp(h):
    fp = dict(h.get("fp") or {})
    if h.get("tty"):
        fp["tty"] = h["tty"]
    return fp


def _prefer_prompted(items, key=lambda x: x[1]):
    """Drop empty sessions (opened and quit at once) when a session with real work also matches."""
    good = [x for x in items if key(x).get("has_prompt") or key(x)["saved"] != "none"]
    return good or items


def place(path):
    """Git work-tree root (realpath) of `path`, or the realpath itself outside git."""
    if not path:
        return None
    try:
        real = os.path.realpath(path)
    except OSError:
        return path
    gd = _git_dir(real)
    if gd and os.path.basename(gd) == ".git":
        return os.path.dirname(gd)
    return real


def _innermost_harness(rt, index):
    """(harness, pid, registry session) of the nearest claude/codex process above us, using the Claude
    registry and process names. (None, None, None) when we are not running inside a session."""
    registry = {s.get("pid"): s for s in index.sessions.values() if s.get("live") and s.get("pid")}
    table = rt.proctable()
    for pid in rt.ancestors():
        if pid in registry:
            return "claude", pid, registry[pid]
        comm = os.path.basename(table.get(pid, (0, ""))[1] or "")
        if comm in ("claude", "codex"):
            return comm, pid, None
    return None, None, None


def _ago(seconds):
    s = int(max(0, seconds))
    if s < 60:
        return "%ds ago" % s
    if s < 3600:
        return "%dm ago" % (s // 60)
    if s < 86400:
        return "%dh ago" % (s // 3600)
    return "%dd ago" % (s // 86400)


def _inside(index, harness, now):
    """Running inside a `harness` session whose id we could not read from the environment."""
    rt = index.rt
    by_ref = index.sessions
    pane = herdr.current_pane(rt)
    if pane and pane.get("session") and pane.get("agent") == harness:
        ref = "%s:%s" % (harness, pane["session"])
        s = by_ref.get(ref) or _placeholder(index, ref)
        return "strong", [_cand(s, "strong", "herdr pane %s, live %s agent" % (pane.get("pane_id"), harness))]
    here = place(rt.env.get("PWD") or os.getcwd())
    live = []
    for s in index.visible(include_empty=True):
        if s["harness"] != harness or place(s.get("cwd")) != here:
            continue
        active = s["times"].get("last_activity_at") or 0
        running = s.get("live") or (harness == "codex" and now - active <= LIVE_WINDOW
                                    and not s["times"].get("ended_at"))
        if running:
            live.append(s)
    live.sort(key=lambda s: s["times"].get("last_activity_at") or 0, reverse=True)
    if len(live) == 1:
        return "strong", [_cand(live[0], "strong", "the running %s session in this folder" % harness)]
    if live:
        return "many", [_cand(s, "weak", "running %s session here" % harness) for s in live[:8]]
    recent = [s for s in index.visible() if s["harness"] == harness][:8]
    return "none", [_cand(s, "weak", "recent") for s in recent]


def which(index, via=None):
    rt = index.rt
    now = rt.now()
    sessions = index.visible(include_empty=True)

    # 1-3: inside a session (skill, bash-mode `!`): the session id is known exactly, or else only
    # that harness's running sessions are candidates
    harness, _pid, reg = _innermost_harness(rt, index)
    if via in ("skill:claude", "skill:codex"):
        harness = via.split(":")[1]
        if reg is not None and harness != "claude":
            reg = None
    if harness:
        src = next(s for s in all_sources() if s.name == harness)
        sid = src.id_from_env(rt.env)
        if sid:
            ref = "%s:%s" % (harness, sid)
            s = index.sessions.get(ref)
            if s is not None and s.get("child") and s["lineage"].get("parent"):
                # a Codex sub-agent thread: the bookmark belongs on the session the user is in
                parent = "%s:%s" % (harness, s["lineage"]["parent"])
                s = index.sessions.get(parent) or _placeholder(index, parent)
                return "exact", [_cand(s, "exact", "inside the session (parent of a sub-agent thread)")]
            s = s or _placeholder(index, ref)
            return "exact", [_cand(s, "exact", "you are inside this session")]
        if reg is not None:
            return "exact", [_cand(reg, "exact", "you are inside this session")]
        return _inside(index, harness, now)

    ppid = rt.ppid()
    mine = _current_fp(rt)
    ends = sorted(((h["at"], s, h) for s in sessions if not s.get("live") for h in s["hooks"]
                   if h["type"] == "hook.end" and now - h["at"] <= WINDOW), key=lambda x: x[0], reverse=True)

    here = place(rt.env.get("PWD") or os.getcwd())

    def superseded(cand, end_at, kind, reason):
        """A newer session ran here after `cand` ended but left no end hook (killed, or Codex before
        its hook is trusted): ask instead of silently saving onto the older session."""
        started_here, hookless = [], []
        for x in sessions:
            # only real work counts: an empty "opened and quit" session never supersedes
            if x is cand or x.get("child") or x.get("live") or not x.get("has_prompt"):
                continue
            later = [h for h in x["hooks"] if h["type"] == "hook.start" and h["at"] > end_at]
            if any(ppid in (h.get("anc") or []) or _fp_ok(mine, _hook_fp(h)) for h in later):
                started_here.append(x)
            elif (not x["hooks"] and place(x.get("cwd")) == here
                  and (x["times"].get("started_at") or 0) > end_at  # a successor, not a parallel pane
                  and (x["times"].get("last_activity_at") or 0) > end_at + 5):
                hookless.append(x)
        if not started_here and not hookless:
            return None
        newer = lambda xs: sorted(xs, key=lambda x: x["times"].get("last_activity_at") or 0, reverse=True)
        first = newer(started_here) + [cand] if started_here else [cand] + newer(hookless)
        return "many", [_cand(x, "weak", reason if x is cand else "ran here after it, no clean exit")
                        for x in first[:8]]

    # 4: the session ran as a child of this very shell
    hits = _prefer_prompted([x for x in ends if ppid in (x[2].get("anc") or [])])
    if hits:
        _at, s, h = hits[0]
        reason = "it ran in this terminal window, ended %s" % _ago(now - h["at"])
        return superseded(s, h["at"], "exact", reason) or ("exact", [_cand(s, "exact", reason)])
    # 5: same pane / tab / terminal, most specific key first; any conflicting key vetoes
    fp_hits = []
    for at, s, h in ends:
        theirs = _hook_fp(h)
        if s["harness"] == "codex" and not theirs:
            continue  # idle-reaper / daemon ends carry no terminal
        key, conflict = fp_match(mine, theirs)
        if key and not conflict:
            fp_hits.append((FP_ORDER.index(key), -at, s, h, key))
    fp_hits.sort(key=lambda x: (x[0], x[1]))
    fp_hits = _prefer_prompted(fp_hits, key=lambda x: x[2])
    if fp_hits:
        best = [x for x in fp_hits if x[0] == fp_hits[0][0]]
        # two sessions match equally (e.g. only the shared tab id) but came from different panes:
        # we cannot tell which one this shell means
        finer = FP_ORDER[:best[0][0]]
        spread = any(len({_hook_fp(x[3]).get(k) for x in best if _hook_fp(x[3]).get(k)}) > 1 for k in finer)
        if spread and len({x[2]["ref"] for x in best}) > 1:
            return "many", [_cand(x[2], "weak", "same %s, but it was a different pane" % best[0][4]) for x in best[:8]]
        _r, _a, s, h, key = fp_hits[0]
        label = {"herdr_pane": "herdr pane", "tmux": "tmux pane", "iterm": "iTerm pane",
                 "term": "terminal tab", "tty": "terminal (%s)" % mine.get("tty")}[key]
        reason = "it ran in this %s, ended %s" % (label, _ago(now - h["at"]))
        return superseded(s, h["at"], "strong", reason) or ("strong", [_cand(s, "strong", reason)])
    # 6: herdr says this pane runs (or just ran) an agent session
    pane = herdr.current_pane(rt)
    if pane and pane.get("session") and pane.get("agent") in ("claude", "codex"):
        ref = "%s:%s" % (pane["agent"], pane["session"])
        s = index.sessions.get(ref) or _placeholder(index, ref)
        return "strong", [_cand(s, "strong", "herdr pane %s" % pane.get("pane_id"))]
    # 7: started in this shell/terminal, never ended cleanly (killed), active recently, not running
    started = []
    for s in sessions:
        if s.get("live") or any(h["type"] == "hook.end" for h in s["hooks"]):
            continue
        starts = [h for h in s["hooks"] if h["type"] == "hook.start"]
        if not starts:
            continue
        h = starts[-1]
        active = s["times"].get("last_activity_at") or 0
        if now - active > WINDOW:
            continue
        key, conflict = fp_match(mine, _hook_fp(h))
        if ppid in (h.get("anc") or []) or (key and not conflict):
            started.append((active, s))
    started.sort(key=lambda x: x[0], reverse=True)
    started = _prefer_prompted(started)
    if started:
        active, s = started[0]
        return "strong", [_cand(s, "strong", "started here, no clean exit, active %s" % _ago(now - active))]
    # 8: same git work tree (or same folder outside git), recently active, not running -> weak
    def elsewhere(s):  # its last hook says it ran in another pane / tab
        hooks = s["hooks"]
        return bool(hooks) and fp_match(mine, _hook_fp(hooks[-1]))[1]
    near = [s for s in sessions if s.get("cwd") and not s.get("live") and s.get("has_prompt")
            and place(s["cwd"]) == here and now - (s["times"].get("last_activity_at") or 0) <= WINDOW
            and not elsewhere(s)]
    if near:
        near.sort(key=lambda s: s["times"].get("ended_at") or s["times"].get("last_activity_at") or 0,
                  reverse=True)
        kind = "weak" if len(near) == 1 else "many"
        return kind, [_cand(s, "weak", "guessed by folder (%s, ended %s)" % (
            _home(rt, s["cwd"]), _ago(now - (s["times"].get("ended_at") or s["times"].get("last_activity_at")))))
            for s in near[:8]]
    # 9: nothing nearby: offer the most recent sessions
    return "none", [_cand(s, "weak", "recent") for s in index.visible()[:8]]


def _placeholder(index, ref):
    from chainlog.fold import _blank, _title
    s = _blank(ref)
    s["title"] = _title(s)
    index.sessions[ref] = s
    return s


def _home(rt, path):
    h = str(rt.home)
    return "~" + path[len(h):] if path and path.startswith(h) else path
