"""ls filters and find scoring."""
from __future__ import annotations

import os
import re

from chainlog import resume
from chainlog.config import parse_duration


def filter_sessions(index, chain=None, pr=None, here=False, since=None, harness=None, unsaved=False,
                    saved=False, include_all=False, starred=False, via=None, kept=False):
    rt = index.rt
    now = rt.now()
    out = index.visible(include_empty=include_all)
    if chain:
        from chainlog.chain import normalize_key
        key = normalize_key(chain)
        bare = re.fullmatch(r"#?(\d+)", key)
        out = [s for s in out if any(c["id"] == key or (bare and c["id"].endswith("#" + bare.group(1)))
                                     for c in s["chains"])]
    if pr:
        from chainlog.chain import make_key, normalize_key
        from chainlog.enrich.git import repo_name
        key = normalize_key(pr)
        repo, _, tail = key.rpartition("#")
        tail = tail or key
        if not tail.isdigit():
            raise ValueError("--pr needs a PR number, e.g. --pr 4321 or --pr shop#4321")
        num = int(tail)

        def num_of(p):
            v = p.get("number")
            return int(v) if str(v).isdigit() else None

        def repo_ok(s):
            if not repo:
                return True
            names = {repo_name((s.get("git") or {}).get("remote"))} | {repo_name(p.get("repo")) for p in s["prs"]}
            return repo in names or any(c["id"] == make_key(repo, num) for c in s["chains"])
        out = [s for s in out if repo_ok(s) and (
            any(num_of(p) == num for p in s["prs"])
            or any(c["id"].endswith("#%d" % num) for c in s["chains"])
            or re.search(r"(?i)\bPR[\s#-]*%d\b" % num, " ".join(str(v) for v in s["titles"].values())))]
    if here:
        cwd = os.path.realpath(rt.env.get("PWD") or os.getcwd())
        from chainlog.enrich.git import remote_url
        my_remote = remote_url(cwd)
        out = [s for s in out if (s.get("cwd") and os.path.realpath(s["cwd"]) == cwd)
               or (my_remote and (s.get("git") or {}).get("remote") == my_remote)]
    if since:
        cutoff = now - parse_duration(since)
        out = [s for s in out if (s["times"].get("last_activity_at") or 0) >= cutoff]
    if harness:
        out = [s for s in out if s["harness"] == harness]
    if via:  # where it was started: terminal | vscode | desktop
        out = [s for s in out if resume.origin(s)[0] == via]
    if unsaved:
        out = [s for s in out if s["saved"] == "none" and not s.get("live")]
    if saved:
        out = [s for s in out if s["saved"] != "none"]
    if starred:
        out = [s for s in out if s.get("starred")]
    if kept:
        out = [s for s in out if s.get("kept")]
    return out


_FIELDS = (
    ("chain", 5), ("pr", 5), ("note", 4), ("title", 3), ("tag", 3), ("branch", 2), ("cwd", 1),
    ("preview", 1), ("origin", 1),
)


def _haystacks(s):
    yield "chain", " ".join(c["id"] for c in s["chains"])
    yield "pr", " ".join("%s#%s pr%s" % (p.get("repo") or "", p.get("number"), p.get("number")) for p in s["prs"])
    yield "note", " ".join(n["text"] for n in s["notes"])
    yield "title", " ".join(str(v) for v in s["titles"].values()) + " " + s["title"]["value"]
    yield "tag", " ".join(s["tags"])
    yield "branch", (s.get("git") or {}).get("branch") or ""
    yield "cwd", os.path.basename((s.get("cwd") or "").rstrip("/"))
    yield "preview", " ".join(x for x in (s.get("last_ask"), s.get("last_work"), s.get("first_ask")) if x)
    key, label = resume.origin(s)  # "vscode" and "VS Code" both find the sessions started in the extension
    yield "origin", " ".join(x for x in (key, label) if x)


STOPWORDS = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "with", "my", "me", "i",
             "where", "which", "that", "this", "was", "is", "it", "session", "sessions", "chat", "find",
             "show", "please", "about", "from", "did", "tested", "testing", "test", "last", "earlier"}


def find(index, text, limit=30, starred=False, every_word=False):
    """Rank sessions by how many search words they contain, then by field weight and recency.
    Filler words ("the session where I tested …") are ignored, so natural phrases work. `every_word`: only sessions that
    contain all of them (picking sessions for a context, where a near miss would be read and merged for nothing)."""
    words = [t for t in re.split(r"[\s,;:!?\"']+", (text or "").lower().strip()) if t]
    tokens = [t for t in words if t not in STOPWORDS] or words
    if not tokens:
        return []
    now = index.rt.now()
    scored = []
    for s in index.visible():
        if starred and not s.get("starred"):
            continue  # a starred-only search ranks among the starred, not among everything
        hay = [(w, val.lower()) for (name, val), (_n, w) in zip(_haystacks(s), _FIELDS)]
        total, matched = 0.0, 0
        for tok in tokens:
            best = 0
            for w, val in hay:
                if tok in val:
                    best = max(best, w * (2 if re.search(r"(?:^|\W)%s(?:\W|$)" % re.escape(tok), val) else 1))
            if best:
                matched += 1
                total += best
        if not matched:
            continue
        if s["saved"] == "user":
            total *= 2
        age_days = max(0.0, (now - (s["times"].get("last_activity_at") or now)) / 86400.0)
        total /= 1.0 + age_days / 30.0
        scored.append((matched, total, s))
    if not scored:
        return []
    top = max(m for m, _t, _s in scored)
    # with several words, drop weak partial matches when some session matches (nearly) all of them
    keep = [x for x in scored if x[0] >= max(1, top - (1 if len(tokens) > 2 else 0))]
    if every_word:
        keep = [x for x in keep if x[0] == len(tokens)]
    keep.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [s for _m, _t, s in keep[:limit]]
