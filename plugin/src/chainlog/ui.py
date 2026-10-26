"""Terminal output: session rows, colours, relative times.

Everything that came from a transcript (titles, previews, notes) goes through safe() before it is
printed: control characters are removed, so a crafted title cannot clear the screen, retitle the
window or write the clipboard (OSC 52). Widths are measured in terminal cells, so CJK and emoji
do not break the row layout.
"""
from __future__ import annotations

import re
import shutil
import time
import unicodedata

MARK = {"user": "●", "auto": "◐", "none": "○"}
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ‎‏‪-‮⁦-⁩]")


def safe(text):
    """One line of printable text: control characters (incl. ESC, bidi overrides) dropped,
    whitespace runs collapsed."""
    if text is None:
        return ""
    return " ".join(_CTRL.sub(" ", str(text)).split())


_SGR = re.compile(r"\x1b\[[0-9;]*m")


def cells(text):
    """Terminal cells `text` occupies: colour codes take none, CJK / wide characters take two."""
    n = 0
    for ch in _SGR.sub("", text):
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def fit(text, width):
    """Cut `text` to at most `width` terminal cells, adding … when cut."""
    if cells(text) <= width:
        return text
    out, n = [], 0
    for ch in text:
        w = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if n + w > width - 1:
            break
        out.append(ch)
        n += w
    return "".join(out) + "…"


def pad(text, width):
    return text + " " * max(0, width - cells(text))


class Style:
    def __init__(self, enabled):
        self.on = enabled

    def _w(self, code, text):
        return "\033[%sm%s\033[0m" % (code, text) if self.on else text

    def dim(self, t):
        return self._w("2", t)

    def bold(self, t):
        return self._w("1", t)

    def green(self, t):
        return self._w("32", t)

    def yellow(self, t):
        return self._w("33", t)

    def red(self, t):
        return self._w("31", t)

    def cyan(self, t):
        return self._w("36", t)


def ago(ts, now=None):
    """Compact age for table columns: 'now', '5m', '3h', '2d', 'Oct 01'."""
    if not ts:
        return "—"
    s = int(max(0, (now or time.time()) - ts))
    if s < 60:
        return "now"
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 86400:
        return "%dh" % (s // 3600)
    if s < 86400 * 60:
        return "%dd" % (s // 86400)
    return time.strftime("%b %d", time.localtime(ts))


def ago_phrase(ts, now=None):
    """Age for sentences: 'just now', '5m ago', 'on Oct 01'."""
    a = ago(ts, now)
    if a == "now":
        return "just now"
    if a == "—":
        return "at an unknown time"
    return ("on " + a) if " " in a else (a + " ago")


def duration(start, end):
    if not start or not end or end < start:
        return ""
    m = int((end - start) // 60)
    return "%dm" % m if m < 120 else "%dh" % (m // 60)


def status_text(st, status):
    if not status:
        return ""
    color = {"pass": st.green, "fail": st.red, "blocked": st.red, "wip": st.yellow}.get(status, st.dim)
    return color(status)


def headline(s):
    """What a row shows: the latest user note, else the auto summary, else the title."""
    notes = [n for n in s["notes"] if n.get("kind") == "note"]
    if notes:
        text = notes[-1]["text"]
    elif s.get("summary"):
        text = s["summary"]["text"]
    else:
        text = s["title"]["value"]
    text = safe(text)
    if not s["transcript"].get("present", True):
        text = "[transcript gone] " + text
    return text


def chain_label(s):
    if not s["chains"]:
        return ""
    c = s["chains"][-1]
    return "%s r%s" % (c["id"], c.get("round") or "?")


def term_width():
    w = shutil.get_terminal_size((110, 20)).columns
    return w if w >= 40 else 100  # some terminals / ptys report 0 columns


def row(st, n, s, now, short_w=8, mid_w=None):
    width = term_width()
    mark = MARK.get(s["saved"], "○")
    if s.get("live"):
        mark = st.green("▶")
    elif s["saved"] == "user":
        mark = st.cyan(mark)
    left = " %s %s %s %-6s " % (st.dim("@%-2d" % n), mark, st.dim(pad(s["short"], short_w)), s["harness"])
    chain = safe(chain_label(s))
    status = s.get("status") or ""
    mid_plain = ("%s %s" % (chain, status)).strip() or "—"
    mid_w = max(mid_w or 0, cells(mid_plain))
    mid = (("%s %s" % (chain, status_text(st, status))).strip() if (chain or status) else st.dim("—"))
    mid += " " * (mid_w - cells(mid_plain))
    age = ago(s["times"].get("last_activity_at"), now)
    used = 1 + 3 + 1 + 1 + 1 + short_w + 1 + 6 + 1 + mid_w + 2 + 2 + cells(age)
    star = s.get("starred")
    text = fit(headline(s), max(12, width - used - (2 if star else 0)))
    return "%s%s  %s%s  %s" % (left, mid, (st.yellow("★") + " ") if star else "", text, st.dim(age))


def layout(sessions):
    """Shared column widths for a block of rows."""
    short_w = max([len(s["short"]) for s in sessions] + [8])
    mid_w = max([cells(("%s %s" % (safe(chain_label(s)), s.get("status") or "")).strip() or "—")
                 for s in sessions] + [1])
    return short_w, min(mid_w, 28)


def preview_lines(st, s, indent="    "):
    out = []
    width = term_width()
    if s.get("last_ask"):
        out.append("%s%s %s" % (indent, st.dim("last ask: "), '"%s"' % fit(safe(s["last_ask"]), width - 20)))
    if s.get("last_work"):
        out.append("%s%s %s" % (indent, st.dim("last work:"), '"%s"' % fit(safe(s["last_work"]), width - 20)))
    return out


def home(rt, path):
    h = str(rt.home)
    path = safe(path)
    return "~" + path[len(h):] if path and path.startswith(h) else (path or "")
