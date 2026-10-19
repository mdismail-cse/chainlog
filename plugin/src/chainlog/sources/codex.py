"""Codex CLI adapter (read-only).

Rollouts: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl and ~/.codex/archived_sessions/.
Titles: ~/.codex/session_index.jsonl ({"id", "thread_name", "updated_at"}).
Child-agent rollouts (session_meta.parent_thread_id set) are never listed.
The *.sqlite state databases are never opened (even read-only they can create -shm files).
"""
from __future__ import annotations

import json
import os
import re

from chainlog.paths import safe_open
from chainlog.redact import clean
from chainlog.sources.base import file_state, iso_to_epoch, iter_json_lines, merge_prev

_ID_IN_NAME = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
# context Codex injects as "user" messages: environment, AGENTS.md, plugin/skill blocks
_INJECTED = ("<", "# AGENTS.md")


def _user_text(payload):
    parts = [x.get("text", "") for x in payload.get("content") or []
             if isinstance(x, dict) and x.get("type") == "input_text"]
    t = " ".join(parts).strip()
    if not t or t.startswith(_INJECTED):
        return None
    return t


class CodexSource:
    name = "codex"
    parser_version = 2

    def discover(self, rt):
        out = []
        for sub in ("sessions", "archived_sessions"):
            root = rt.codex_dir / sub
            for dirpath, _dirs, files in os.walk(root):
                for n in files:
                    if n.startswith("rollout-") and n.endswith(".jsonl"):
                        out.append(os.path.join(dirpath, n))
        return out

    def titles(self, rt):
        out = {}
        try:
            with safe_open(rt.codex_dir / "session_index.jsonl") as f:
                for line in f:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(o, dict) and isinstance(o.get("id"), str) and isinstance(o.get("thread_name"), str) \
                            and o["thread_name"].strip():
                        out[o["id"]] = o["thread_name"]  # later lines win
        except OSError:
            pass
        return out

    def parse(self, path, prev, rt, snippets=True):
        try:
            state = file_state(path, self.parser_version)
        except OSError:
            return None, None
        m = _ID_IN_NAME.search(path)
        f = {"harness": "codex", "id": m.group(1) if m else os.path.basename(path), "path": path,
             "titles": {}, "prs": [], "last_activity": state["mtime"],
             "archived": os.sep + "archived_sessions" + os.sep in path}
        meta_seen = False
        last_ts = None
        try:
            for part, o in iter_json_lines(path, state["size"]):
                try:
                    t = o.get("type")
                    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
                    if o.get("timestamp"):
                        last_ts = o["timestamp"]
                    if t == "session_meta" and not meta_seen:
                        meta_seen = True
                        if isinstance(p.get("id"), str) and p["id"]:
                            f["id"] = p["id"]
                        f["cwd"] = p.get("cwd") if isinstance(p.get("cwd"), str) else None
                        f["started_at"] = iso_to_epoch(p.get("timestamp"))
                        f["version"] = p.get("cli_version")
                        src = p.get("source")
                        f["entrypoint"] = src if isinstance(src, str) else "subagent"
                        f["originator"] = p.get("originator")
                        f["parent"] = p.get("parent_thread_id")
                        f["forked_from"] = p.get("forked_from_id")
                        f["child"] = bool(p.get("parent_thread_id")) or isinstance(src, dict) \
                            or p.get("thread_source") == "subagent"
                        git = p.get("git") if isinstance(p.get("git"), dict) else {}
                        if git.get("branch"):
                            f["branch"] = git["branch"]
                        if git.get("repository_url"):
                            f["remote"] = git["repository_url"]
                    elif t == "event_msg":
                        st = p.get("type")
                        if st == "user_message" and isinstance(p.get("message"), str) and p["message"].strip():
                            f["has_prompt"] = True
                            if not f.get("first_ask"):
                                f["first_ask"] = p["message"]
                            f["last_ask"] = p["message"]
                        elif st == "agent_message" and isinstance(p.get("message"), str) and p["message"].strip():
                            f["last_work"] = p["message"]
                        elif st == "task_complete" and isinstance(p.get("last_agent_message"), str) \
                                and p["last_agent_message"].strip():
                            f["last_work"] = p["last_agent_message"]
                    elif t == "response_item" and p.get("type") == "message" and p.get("role") == "user":
                        u = _user_text(p)
                        if u:
                            f["has_prompt"] = True
                            if not f.get("first_ask"):
                                f["first_ask"] = u
                            f["last_ask"] = u
                    elif t == "turn_context" and p.get("cwd") and not f.get("cwd"):
                        f["cwd"] = p["cwd"]
                except (AttributeError, TypeError, ValueError, KeyError):
                    continue  # one odd line must never break indexing
        except OSError:
            f["warnings"] = ["unreadable"]
        if last_ts:
            f["last_ts"] = iso_to_epoch(last_ts)
        f = merge_prev(f, prev, ("cwd", "started_at", "version", "entrypoint", "first_ask", "has_prompt",
                                 "branch", "remote", "child", "parent", "forked_from", "originator"))
        f["interactive"] = (f.get("entrypoint") in ("cli", "vscode")) and not f.get("child")
        if snippets:
            f["last_ask"] = clean(f.get("last_ask"), 160)
            f["last_work"] = clean(f.get("last_work"), 160)
            f["first_ask"] = clean(f.get("first_ask"), 120)
        else:
            f["last_ask"] = f["last_work"] = f["first_ask"] = ""
        return f, state

    def live(self, rt):
        return []  # no registry; herdr enrichment covers live Codex panes

    def id_from_env(self, env):
        v = env.get("CODEX_THREAD_ID") or ""
        return v if re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$", v) else None

    def resume_argv(self, native_id, fork=False):
        return ["codex", "fork" if fork else "resume", native_id]
