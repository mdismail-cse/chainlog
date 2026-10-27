"""Delete a session from Claude's side, chainlog's side, or both (`chainlog delete`).

Claude's side is the transcript and the folder of sub-agent output next to it, nothing else, and only when the path is
exactly <claude dir>/projects/<project>/<session id>.jsonl. chainlog's side is the kept copy plus hiding the session
(`forget`, which `chainlog undo` reverses; the log of events is append-only and is not rewritten).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from chainlog import keep

WHERE = ("both", "claude", "chainlog")
_ID = keep._ID


def _size(path):
    if os.path.isfile(path) and not os.path.islink(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def claude_targets(rt, s):
    """The files Claude keeps for this session that may be deleted: the transcript and its <id>/ folder."""
    path, nid = s["transcript"].get("path"), s.get("native_id") or ""
    if s["harness"] != "claude" or not path or not _ID.match(nid):
        return []
    p = Path(path)
    projects = Path(os.path.realpath(rt.claude_dir / "projects"))
    if p.name != nid + ".jsonl" or Path(os.path.realpath(p.parent)).parent != projects or os.path.islink(p):
        return []  # not where a Claude transcript lives: leave it alone
    out = [p] if os.path.isfile(p) else []
    folder = p.with_suffix("")
    if os.path.isdir(folder) and not os.path.islink(folder):
        out.append(folder)
    return out


def plan(rt, s, where="both"):
    """What deleting would remove: {"claude": [(path, bytes)], "kept": bytes, "chainlog": bool, "refused": why or None}."""
    refused = None
    if s.get("live"):
        refused = "it is still running; close it first"
    targets = claude_targets(rt, s) if where in ("both", "claude") else []
    if where in ("both", "claude") and s["harness"] != "claude":
        refused = refused or "chainlog only deletes Claude's own files; for a Codex session use --from chainlog"
    kept = 0
    if where in ("both", "chainlog") and keep.supported(s):
        kept = sum(os.path.getsize(p) for p in keep.files(rt, s) if os.path.isfile(p))
    return {"claude": [(str(t), _size(t)) for t in targets], "kept": kept,
            "chainlog": where in ("both", "chainlog"), "refused": refused}


def carry_out(rt, s, where="both"):
    """Do it. Returns {"claude_bytes": n, "kept_bytes": n}. The caller records the `forget` / `unkeep` events."""
    freed = {"claude_bytes": 0, "kept_bytes": 0}
    if where in ("both", "claude"):
        for t in claude_targets(rt, s):
            n = _size(t)
            if os.path.isdir(t):
                shutil.rmtree(t)
            else:
                os.unlink(t)
            freed["claude_bytes"] += n
    if where in ("both", "chainlog"):
        freed["kept_bytes"] = keep.remove(rt, s)
    return freed
