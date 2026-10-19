"""herdr enrichment: CLI only (never from a hook), 500 ms timeout, silent when herdr is absent.

chainlog never edits herdr's hook scripts or hook entries.
"""
from __future__ import annotations

import json


def binary(rt):
    return rt.env.get("HERDR_BIN_PATH") or "herdr"


def available(rt):
    return bool(rt.env.get("HERDR_PANE_ID") or rt.env.get("HERDR_ENV"))


def current_pane(rt):
    """The pane this command runs in, or None. Keys: pane_id, agent, session, status, title, cwd."""
    if not available(rt):
        return None
    rc, out, _ = rt.run([binary(rt), "pane", "current"], timeout=0.5)
    if rc != 0:
        return None
    try:
        pane = (json.loads(out).get("result") or {}).get("pane") or {}
    except (ValueError, AttributeError):
        return None
    sess = pane.get("agent_session") or {}
    return {
        "pane_id": pane.get("pane_id"),
        "agent": pane.get("agent") or sess.get("agent"),
        "session": sess.get("value"),
        "status": pane.get("agent_status"),
        "title": pane.get("terminal_title_stripped"),
        "cwd": pane.get("cwd"),
    }


def notify(rt, title, body):
    rc, _, _ = rt.run([binary(rt), "notification", "show", title, "--body", body, "--sound", "request"],
                      timeout=1.5)
    return rc == 0
