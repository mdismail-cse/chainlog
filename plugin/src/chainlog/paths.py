"""Safe file access.

Every file chainlog opens goes through safe_open(). It refuses the secret / private files of the
harnesses outright, and refuses any write under ~/.claude or ~/.codex unless the caller is a setup
integration that passes allow_harness_write=True (and has shown the user a diff first).
"""
from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path

# Never opened, for any reason.
DENY_GLOBS = (
    "*.key",
    "*/.codex/auth.json",
    "*/.claude/history.jsonl",
    "*/.codex/history.jsonl",
    "*/.codex/*.sqlite*",
    "*/.claude/.credentials.json",
)


class SafetyError(PermissionError):
    pass


def is_denied(path) -> bool:
    """Checked on the given path AND on what it resolves to, so a symlink cannot smuggle a denied
    file (e.g. a transcript symlinked to ~/.codex/auth.json) past the list."""
    candidates = {os.path.abspath(str(path))}
    try:
        candidates.add(os.path.realpath(str(path)))
    except OSError:
        pass
    return any(fnmatch.fnmatch(p, g) for p in candidates for g in DENY_GLOBS)


def _under(path, root) -> bool:
    try:
        Path(os.path.abspath(str(path))).relative_to(os.path.abspath(str(root)))
        return True
    except ValueError:
        return False


def safe_open(path, mode="r", rt=None, allow_harness_write=False, **kw):
    if is_denied(path):
        raise SafetyError("chainlog never opens %s" % path)
    writing = any(c in mode for c in "wax+")
    if writing and rt is not None and not allow_harness_write:
        for root in (rt.claude_dir, rt.codex_dir):
            if _under(path, root):
                raise SafetyError("chainlog does not write under %s outside setup" % root)
    if "b" not in mode:
        kw.setdefault("encoding", "utf-8")
        kw.setdefault("errors", "replace")
    return open(path, mode, **kw)


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def atomic_write(path: Path, data: str, rt=None, allow_harness_write=False, mode=None):
    """tmp + fsync + rename in the same directory.

    A symlinked target (e.g. ~/.zshrc -> ~/dotfiles/zshrc) is written through, so the link survives.
    An existing file keeps its permission bits; new files default to 0600. Text is encoded with
    surrogateescape, so bytes that are not valid UTF-8 round-trip unchanged.
    """
    path = Path(path)
    if os.path.islink(path):
        path = Path(os.path.realpath(path))
    if mode is None:
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            mode = 0o600
    tmp = path.with_name(".%s.tmp.%d" % (path.name, os.getpid()))
    with safe_open(tmp, "w", rt=rt, allow_harness_write=allow_harness_write, errors="surrogateescape",
                   newline="") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)
