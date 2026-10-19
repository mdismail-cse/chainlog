"""config.json: user settings, including the unsaved-session policy (plan §3.7)."""
from __future__ import annotations

import copy
import json
import re
import sys

from chainlog.paths import atomic_write, ensure_private_dir, safe_open

ACTIONS = ("summarize", "list", "remind", "off")
ENGINES = ("local", "auto", "claude", "codex")

DEFAULTS = {
    "v": 1,
    "snippets": True,
    "auto_chain": True,
    "unsaved": {"action": "list", "after": 600, "lookback": 86400},
    "summary": {"engine": "local", "timeout": 60},
    "installed_at": None,
}

# dotted key -> (type, validator description)
KEYS = {
    "unsaved.action": "one of: summarize, list, remind, off",
    "unsaved.after": "duration, e.g. 10m (1m … 24h)",
    "unsaved.lookback": "duration, e.g. 24h",
    "summary.engine": "one of: local, auto, claude, codex",
    "summary.timeout": "seconds",
    "snippets": "true/false (store redacted last-ask / last-work previews)",
    "auto_chain": "true/false (link saves to a PR chain automatically)",
}


def parse_duration(text) -> int:
    if isinstance(text, (int, float)):
        return int(text)
    m = re.fullmatch(r"\s*(\d+)\s*([smhd]?)\s*", str(text))
    if not m:
        raise ValueError("not a duration: %r (use e.g. 90s, 10m, 2h, 1d)" % text)
    n, unit = int(m.group(1)), m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def fmt_duration(seconds) -> str:
    s = int(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size and s % size == 0:
            return "%d%s" % (s // size, unit)
    return "%ds" % s


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_TYPES = {"unsaved.action": lambda v: v in ACTIONS, "summary.engine": lambda v: v in ENGINES,
          "unsaved.after": lambda v: isinstance(v, int) and 60 <= v <= 86400,
          "unsaved.lookback": lambda v: isinstance(v, int) and v >= 60,
          "summary.timeout": lambda v: isinstance(v, int) and 5 <= v <= 600,
          "snippets": lambda v: isinstance(v, bool), "auto_chain": lambda v: isinstance(v, bool)}


def load(rt) -> dict:
    path = rt.data_home / "config.json"
    data = {}
    try:
        with safe_open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        sys.stderr.write("chainlog: %s is not valid JSON (%s); using defaults until it is fixed or deleted\n"
                         % (path, e))
        cfg = _merge(DEFAULTS, {})
        cfg["_broken"] = True
        return cfg
    cfg = _merge(DEFAULTS, data if isinstance(data, dict) else {})
    for key, ok in _TYPES.items():  # a wrong-typed value falls back to its default, never crashes
        try:
            if not ok(get(cfg, key)):
                raise KeyError(key)
        except KeyError:
            sys.stderr.write("chainlog: ignoring invalid setting %s in config.json\n" % key)
            cfg = _put(cfg, key, get(DEFAULTS, key))
    return cfg


def _put(cfg, key, value):
    cfg = copy.deepcopy(cfg)
    cur = cfg
    parts = key.split(".")
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value
    return cfg


def show(key, value):
    if key in ("unsaved.after", "unsaved.lookback") and isinstance(value, int):
        return fmt_duration(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def save(rt, cfg: dict):
    if cfg.get("_broken"):
        raise ValueError("config.json is not valid JSON — fix or delete %s first" % (rt.data_home / "config.json"))
    home = ensure_private_dir(rt.data_home)
    atomic_write(home / "config.json", json.dumps(cfg, indent=2) + "\n", rt=rt)
    # plain-seconds copy so the sh hook can compute the due time with the `read` builtin
    atomic_write(home / "after", "%d\n" % int(cfg["unsaved"]["after"]), rt=rt)


def get(cfg, key):
    cur = cfg
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(key)
        cur = cur[part]
    return cur


def set_value(cfg, key, raw) -> dict:
    if key not in KEYS:
        raise KeyError("unknown setting %r. Known: %s" % (key, ", ".join(sorted(KEYS))))
    if key == "unsaved.action":
        if raw not in ACTIONS:
            raise ValueError("unsaved.action must be one of %s" % ", ".join(ACTIONS))
        value = raw
    elif key == "summary.engine":
        if raw not in ENGINES:
            raise ValueError("summary.engine must be one of %s" % ", ".join(ENGINES))
        value = raw
    elif key == "unsaved.after":
        value = parse_duration(raw)
        if not 60 <= value <= 86400:
            raise ValueError("unsaved.after must be between 1m and 24h")
        if value > cfg["unsaved"]["lookback"]:
            raise ValueError("unsaved.after must not exceed unsaved.lookback (%s)" % fmt_duration(cfg["unsaved"]["lookback"]))
    elif key == "unsaved.lookback":
        value = parse_duration(raw)
        if value < cfg["unsaved"]["after"]:
            raise ValueError("unsaved.lookback must be at least unsaved.after (%s)" % fmt_duration(cfg["unsaved"]["after"]))
    elif key == "summary.timeout":
        try:
            value = int(raw)
        except ValueError:
            raise ValueError("summary.timeout must be a number of seconds")
        if not 5 <= value <= 600:
            raise ValueError("summary.timeout must be between 5 and 600 seconds")
    else:
        v = str(raw).strip().lower()
        if v in ("1", "true", "yes", "on"):
            value = True
        elif v in ("0", "false", "no", "off"):
            value = False
        else:
            raise ValueError("%s must be true or false (got %r)" % (key, raw))
    return _put(cfg, key, value)
