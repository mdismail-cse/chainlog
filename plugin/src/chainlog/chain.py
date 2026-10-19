"""PR chains: an ordered timeline of sessions (rounds) for one PR, across harnesses.

A chain key is "<repo>#<number>" (repo = the last path segment of the git remote, lowercased),
or free text for non-PR chains (`-c "release-3.2 regression"`).
"""
from __future__ import annotations

import os
import re

from chainlog.enrich.git import repo_name

_PR_IN_TEXT = re.compile(r"(?i)\bPR[\s#-]*(\d{3,6})\b")
_PR_IN_BRANCH = re.compile(r"(?i)(?:^|[/_-])(?:pr|pull)[/_-]?(\d{3,6})(?:$|[/_-])")
_PR_URL = re.compile(r"(?i)^(?:https?://)?[^/\s]+/([^/\s]+)/([^/\s]+?)(?:\.git)?/pull/(\d{1,7})(?:[/?#].*)?$")
_SLUG = re.compile(r"^(?:[A-Za-z0-9_.-]+/)?([A-Za-z0-9_.-]+)#(\d{1,7})$")
_BARE = re.compile(r"(?i)^(?:#|pr[\s#-]*)?(\d{1,7})$")
_CHAIN_REF = re.compile(r"^(?:(?:[A-Za-z0-9_.-]+/)?(?P<repo>[A-Za-z0-9_.-]+)#|#)?(?P<num>\d{1,7})(?:/(?P<round>\d+))?$")


def _clean_repo(repo):
    r = re.sub(r"[^a-z0-9_.-]+", "-", (repo or "").lower()).strip("-.")
    return r or "pr"


def make_key(repo, number):
    return "%s#%s" % (_clean_repo(repo), int(number))


def strong_repo(session):
    """Repo name from evidence: the git remote, else a Claude pr-link. None if neither exists."""
    g = session.get("git") or {}
    r = repo_name(g.get("remote"))
    if r:
        return r
    for pr in reversed(session.get("prs") or []):
        r = repo_name(pr.get("repo"))
        if r:
            return r
    return None


def _folder(session):
    return repo_name(os.path.basename((session.get("cwd") or "").rstrip("/"))) or "pr"


def suggest_key(session):
    """Chain key for a session from strong evidence only, or (None, None):
    a Claude pr-link, or 'PR #n' in a title / branch when the git remote names the repo."""
    for pr in reversed(session.get("prs") or []):
        if pr.get("source") == "pr-link" and pr.get("number"):
            repo = repo_name(pr.get("repo")) or strong_repo(session)
            if repo:
                return make_key(repo, pr["number"]), "pr-link"
    repo = repo_name((session.get("git") or {}).get("remote"))
    if not repo:
        return None, None  # a folder name is not evidence: clones and worktrees would split chains
    titles = session.get("titles") or {}
    for src in ("user", "custom", "agent", "ai", "thread"):
        t = titles.get(src)
        m = _PR_IN_TEXT.search(t) if isinstance(t, str) else None
        if m:
            return make_key(repo, m.group(1)), "title"
    m = _PR_IN_BRANCH.search(((session.get("git") or {}).get("branch")) or "")
    if m:
        return make_key(repo, m.group(1)), "branch"
    return None, None


_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def normalize_key(text):
    """User-typed key without session context: URL / owner/repo#N / repo#N -> 'repo#n'; free text
    trimmed, lowercased and stripped of control characters. A '/<round>' suffix is dropped (rounds
    are ordered by start time, not chosen when saving)."""
    t = " ".join(_CTRL.sub(" ", str(text)).split())
    t = re.sub(r"(#\d{1,7})/\d+$", r"\1", t)
    m = _PR_URL.match(t)
    if m:
        return make_key(m.group(2), m.group(3))
    m = _SLUG.match(t)
    if m:
        return make_key(m.group(1), m.group(2))
    return t.lower()


def key_from_arg(arg, session, chain_ids):
    """`-c` value -> chain key. A bare number ('4321', '#4321', 'PR 4321') means *this session's*
    repo; only when the session has no repo evidence does it join the one existing '*#4321' chain."""
    t = " ".join(_CTRL.sub(" ", str(arg)).split())
    t = re.sub(r"(\d)/\d+$", r"\1", t)  # 'shop#4321/2' or '4321/2': the round is not a key
    m = _BARE.match(t)
    if m:
        n = int(m.group(1))
        repo = strong_repo(session)
        if repo:
            return make_key(repo, n)
        hits = sorted(c for c in chain_ids if c.endswith("#%d" % n))
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise ValueError("-c %s matches several chains (%s) and this session has no git remote: "
                             "use the full key, e.g. -c %s" % (t, ", ".join(hits), hits[0]))
        return make_key(_folder(session), n)
    return normalize_key(t)


def parse_ref(text):
    """('shop', 4321, 2) for 'shop#4321/2' (also 'acme/Shop#4321/2'); (None, 4321, None) for
    '4321' or '#4321'; None if not a chain ref."""
    m = _CHAIN_REF.match(str(text).strip())
    if not m:
        return None
    return (_clean_repo(m.group("repo")) if m.group("repo") else None, int(m.group("num")),
            int(m.group("round")) if m.group("round") else None)


def build_chains(sessions):
    """Chains from the sessions' chain memberships. Rounds are ordered by start time unless pinned."""
    chains = {}
    for s in sessions.values():
        if s.get("hidden"):
            continue
        for link in s.get("chains") or []:
            c = chains.setdefault(link["id"], {"id": link["id"], "members": []})
            c["members"].append((link.get("pos"), (s["times"].get("started_at") or 0), s["ref"]))
    for c in chains.values():
        c["members"].sort(key=lambda m: (m[0] if m[0] is not None else 10 ** 9, m[1]))
        c["members"] = [{"ref": ref, "round": i + 1} for i, (_p, _t, ref) in enumerate(c["members"])]
        statuses = [sessions[m["ref"]].get("status") for m in c["members"]]
        c["status"] = next((x for x in reversed(statuses) if x), None)  # the latest round's verdict
        last = sessions[c["members"][-1]["ref"]]
        c["last_activity"] = max((sessions[m["ref"]]["times"].get("last_activity_at") or 0) for m in c["members"])
        c["url"] = next((p.get("url") for m in c["members"] for p in (sessions[m["ref"]].get("prs") or [])
                         if p.get("url") and p.get("number") and c["id"].endswith("#%s" % p["number"])), None)
        c["title"] = last["title"]["value"]
        for m in c["members"]:
            for link in sessions[m["ref"]]["chains"]:
                if link["id"] == c["id"]:
                    link["round"] = m["round"]
    return chains
