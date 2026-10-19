"""Reminders: desktop notification + herdr toast. Bodies carry title/duration/short id only,
never preview text (notifications can show on a locked screen)."""
from __future__ import annotations

import shutil
import sys

from chainlog.enrich import herdr


def desktop(rt, title, body):
    if sys.platform == "darwin":
        # argv-passing form: no string is ever interpolated into AppleScript source
        rc, _, _ = rt.run(["osascript", "-e", "on run argv", "-e",
                           "display notification (item 2 of argv) with title (item 1 of argv)",
                           "-e", "end run", title, body], timeout=3)
        return rc == 0
    if shutil.which("notify-send"):
        rc, _, _ = rt.run(["notify-send", title, body], timeout=3)
        return rc == 0
    return False


def remind(rt, title, body):
    used = []
    if desktop(rt, title, body):
        used.append("desktop")
    if shutil.which(herdr.binary(rt)) or rt.env.get("HERDR_BIN_PATH"):
        if herdr.notify(rt, title, body):
            used.append("herdr")
    return used
