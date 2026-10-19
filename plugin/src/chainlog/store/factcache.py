"""factcache.json: per-transcript facts keyed by path, with the file state they were parsed from.

Entirely rebuildable: deleting the file only costs one slower run.
"""
from __future__ import annotations

import json

from chainlog.paths import atomic_write, ensure_private_dir, safe_open

SCHEMA = 2


def load(rt) -> dict:
    try:
        with safe_open(rt.data_home / "factcache.json") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            return data.get("files") or {}
    except (OSError, ValueError):
        pass
    return {}


def save(rt, files: dict):
    ensure_private_dir(rt.data_home)
    atomic_write(rt.data_home / "factcache.json",
                 json.dumps({"schema": SCHEMA, "files": files}, ensure_ascii=False, separators=(",", ":")),
                 rt=rt)
