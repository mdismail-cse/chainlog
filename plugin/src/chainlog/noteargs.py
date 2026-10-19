"""--args-stdin: parse '/chainlog:save r2 refund 500 -s fail' text without a shell.

Only recognised flag pairs at the very start or end are taken as flags; everything else is the
note, verbatim (internal spacing and quotes preserved). A leading `--` turns flag parsing off.
No shlex: apostrophes, quotes and $(…) in notes are data, never code.
"""
from __future__ import annotations

import re

_PAIR = r"(?P<flag>-s|--status|-c|--chain|-t|--tag)\s+(?P<val>\S+)"
_LEAD_PAIR = re.compile(r"^\s*" + _PAIR + r"(?=\s|$)")
_TAIL_PAIR = re.compile(r"(?:^|\s)" + _PAIR + r"\s*$")
_LEAD_NOCHAIN = re.compile(r"^\s*--no-chain(?=\s|$)")
_TAIL_NOCHAIN = re.compile(r"(?:^|\s)--no-chain\s*$")

FLAG_NAMES = {"-s": "status", "--status": "status", "-c": "chain", "--chain": "chain",
              "-t": "tag", "--tag": "tag"}
STATUSES = ("wip", "pass", "fail", "blocked")
# a flag only counts when its value has the right shape; otherwise the words stay in the note
# ("… then curl -s https://x" is a note, not a status)
_VALID = {
    "status": lambda v: v.lower() in STATUSES,
    "chain": re.compile(r"(?i)^(?:(?:[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+#\d{1,7}|#?\d{1,7}|"
                        r"https?://\S+/pull/\d{1,7}\S*)$").match,
    "tag": re.compile(r"^[A-Za-z][\w.-]{0,30}$").match,
}


def parse(text):
    """Returns dict(note, status, chain, tags, no_chain)."""
    out = {"note": "", "status": None, "chain": None, "tags": [], "no_chain": False}
    text = (text or "").replace("\r\n", "\n").strip("\n")
    if text.lstrip().startswith("-- ") or text.strip() == "--":
        out["note"] = text.lstrip()[2:].strip()
        return out

    def take(m):
        name, val = FLAG_NAMES[m.group("flag")], m.group("val")
        if not _VALID[name](val):
            return False
        if name == "tag":
            out["tags"].append(val)
        elif name == "status":
            out["status"] = val.lower()
        else:
            out[name] = val
        return True

    while True:
        m = _LEAD_PAIR.match(text)
        if m and take(m):
            text = text[m.end():]
            continue
        m = _LEAD_NOCHAIN.match(text)
        if m:
            out["no_chain"] = True
            text = text[m.end():]
            continue
        break
    while True:
        m = _TAIL_PAIR.search(text)
        if m and take(m):
            text = text[: m.start()]
            continue
        m = _TAIL_NOCHAIN.search(text)
        if m:
            out["no_chain"] = True
            text = text[: m.start()]
            continue
        break
    out["note"] = text.strip()
    return out
