"""Git remote lookup by reading .git/config (no subprocess)."""
from __future__ import annotations

import os
import re

from chainlog.paths import safe_open

_REMOTE = re.compile(r'^\s*\[remote\s+"([^"]+)"\]\s*$')
_URL = re.compile(r"^\s*url\s*=\s*(\S+)\s*$")

_cache = {}


def _git_dir(cwd):
    d = os.path.abspath(cwd)
    while True:
        g = os.path.join(d, ".git")
        if os.path.isdir(g):
            return g
        if os.path.isfile(g):  # worktree / submodule: "gitdir: <path>"
            try:
                with safe_open(g) as f:
                    line = f.readline().strip()
                if line.startswith("gitdir:"):
                    gd = line.split(":", 1)[1].strip()
                    gd = gd if os.path.isabs(gd) else os.path.join(d, gd)
                    common = os.path.join(gd, "commondir")
                    if os.path.isfile(common):
                        with safe_open(common) as f:
                            c = f.read().strip()
                        return os.path.normpath(c if os.path.isabs(c) else os.path.join(gd, c))
                    return gd
            except OSError:
                return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def remote_url(cwd):
    if not cwd:
        return None
    if cwd in _cache:
        return _cache[cwd]
    url = None
    gd = _git_dir(cwd)
    if gd:
        remotes = {}
        try:
            with safe_open(os.path.join(gd, "config")) as f:
                cur = None
                for line in f:
                    m = _REMOTE.match(line)
                    if m:
                        cur = m.group(1)
                        continue
                    if line.lstrip().startswith("["):
                        cur = None
                    m = _URL.match(line)
                    if m and cur:
                        remotes.setdefault(cur, m.group(1))
        except OSError:
            pass
        url = remotes.get("origin") or next(iter(remotes.values()), None)
    _cache[cwd] = url
    return url


def repo_name(url_or_slug):
    """'git@github.com:org/Shop.git' / 'org/shop' / 'https://…/shop' -> 'shop'."""
    if not url_or_slug:
        return None
    s = str(url_or_slug).rstrip("/")
    s = s[:-4] if s.endswith(".git") else s
    s = re.split(r"[/:]", s)[-1]
    return s.lower() or None


def repo_slug(url):
    """'git@github.com:org/shop.git' -> 'org/shop' (for building PR URLs)."""
    if not url:
        return None
    s = str(url).rstrip("/")
    s = s[:-4] if s.endswith(".git") else s
    parts = re.split(r"[/:]", s)
    return "/".join(parts[-2:]) if len(parts) >= 2 else None
