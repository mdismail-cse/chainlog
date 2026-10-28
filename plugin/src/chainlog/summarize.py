"""Session summaries for the unsaved-session policy (plan §3.7).

local  – deterministic, offline: title, last ask → last reply, PR/branch, diff stats, duration.
claude – `claude -p` (Haiku) over a short redacted excerpt of user/assistant TEXT blocks only.
codex  – `codex exec --ephemeral` over the same excerpt.
auto   – the harness that ran the session, so session text never crosses providers.
Any engine failure falls back to local and records why.
"""
from __future__ import annotations

import os
import tempfile

from chainlog.redact import clean, redact
from chainlog.sources.base import iter_json_lines

PROMPT = ("You are summarizing a coding/QA agent session for the user's own session log. "
          "In at most 25 words, plain text, no preamble: what was tested or built, the current "
          "result, and the next step.")
EXCERPT_MAX = 12000


def local_summary(s, limit=200):
    parts = []
    title = s["title"]["value"]
    if title:
        parts.append(title)
    ask, work = s.get("last_ask"), s.get("last_work")
    if ask and work:
        parts.append('last: "%s" → "%s"' % (clean(ask, 60), clean(work, 70)))
    elif work or ask:
        parts.append('last: "%s"' % clean(work or ask, 90))
    branch = (s.get("git") or {}).get("branch")
    if branch and branch not in ("HEAD", "main", "master"):
        parts.append(branch)
    st = s.get("stats") or {}
    if st.get("lines_added") or st.get("lines_removed"):
        parts.append("+%s/−%s" % (st.get("lines_added") or 0, st.get("lines_removed") or 0))
    start = s["times"].get("started_at")
    end = s["times"].get("ended_at") or s["times"].get("last_activity_at")
    if start and end and end > start:
        m = int((end - start) // 60)
        parts.append("%dm" % m if m < 120 else "%dh" % (m // 60))
    return clean(" · ".join(parts), limit)


def text_blocks(s):
    """[("USER" | "ASSISTANT", text)] from the transcript, oldest first; no tool output; [] when it cannot be read."""
    path = (s.get("transcript") or {}).get("path")
    if not path or not os.path.isfile(path):
        return []
    blocks = []
    try:
        size = os.path.getsize(path)
        if s["harness"] == "claude":
            from chainlog.sources.claude import _assistant_text, _user_text
            for _part, o in iter_json_lines(path, size):
                u = _user_text(o)
                if u:
                    blocks.append(("USER", u))
                    continue
                a = _assistant_text(o)
                if a:
                    blocks.append(("ASSISTANT", a))
        else:
            from chainlog.sources.codex import _user_text as codex_user
            for _part, o in iter_json_lines(path, size):
                p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
                if o.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user":
                    u = codex_user(p)
                    if u:
                        blocks.append(("USER", u))
                elif o.get("type") == "event_msg" and p.get("type") == "agent_message" and p.get("message"):
                    blocks.append(("ASSISTANT", p["message"]))
    except OSError:
        return []
    return blocks


def excerpt(s, max_chars=EXCERPT_MAX):
    """Last user/assistant text blocks from the transcript tail; no tool output, redacted."""
    blocks = ["%s: %s" % b for b in text_blocks(s)]
    out, total = [], 0
    for b in reversed(blocks[-30:]):
        b, _ = redact(" ".join(b.split())[:2000])
        if total + len(b) > max_chars:
            break
        out.append(b)
        total += len(b)
    return "\n".join(reversed(out))


# What the AI is run with. It is given text and answers with text: no file, shell or connector tool, and no user config
# (which can name MCP servers, hooks and plugins). Without these flags `claude -p` can read files in its folder and use
# every connector the account has (Docs, Slack, ...), and `codex exec -s read-only` can still run shell commands, so
# text recorded in an old session could make either one do things.
CLAUDE_NO_TOOLS = ["--tools", "", "--strict-mcp-config", "--setting-sources", "project"]
CODEX_NO_TOOLS = ["--ignore-user-config", "--disable", "shell_tool", "--disable", "plugins", "--disable", "apps",
                  "--disable", "hooks", "-s", "read-only"]


def _child_env(rt):
    env = {k: v for k, v in rt.env.items()
           if not k.startswith("HERDR_") and k not in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID",
                                                        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env["CHAINLOG_SUMMARIZER"] = "1"
    return env


def _run_claude(rt, text, timeout):
    with tempfile.TemporaryDirectory(prefix="chainlog-sum-") as tmp:
        rc, out, err = rt.run(["claude", "-p", "--no-session-persistence", *CLAUDE_NO_TOOLS,
                               "--model", "haiku", "--output-format", "text", PROMPT],
                              timeout=timeout, input=text, env=_child_env(rt), cwd=tmp)
    if rc != 0:
        return None, "claude exited %s: %s" % (rc, redact(" ".join((err or out).split()))[0][:120])
    if not out.strip():
        return None, "claude returned an empty summary"
    return out.strip(), None


def _run_codex(rt, text, timeout):
    with tempfile.TemporaryDirectory(prefix="chainlog-sum-") as tmp:
        last = os.path.join(tmp, "last.txt")
        rc, out, err = rt.run(["codex", "exec", "--ephemeral", "--skip-git-repo-check", *CODEX_NO_TOOLS,
                               "-C", tmp, "-o", last, "-"],
                              timeout=timeout, input=PROMPT + "\n\n<session>\n" + text + "\n</session>\n",
                              env=_child_env(rt), cwd=tmp)
        msg = None
        if os.path.isfile(last):
            with open(last, encoding="utf-8", errors="replace") as f:
                msg = f.read().strip()
    if rc != 0 or not msg:
        return None, "codex exited %s%s: %s" % (rc, "" if msg else " (no summary)",
                                               redact(" ".join((err or out).split()))[0][-120:])
    return msg, None


def summarize(rt, s, cfg):
    """Returns (text, engine_used, fallback_reason_or_None)."""
    engine = (cfg.get("summary") or {}).get("engine", "local")
    timeout = int((cfg.get("summary") or {}).get("timeout", 60))
    if engine == "auto":
        engine = s["harness"]
    if engine == "local":
        return local_summary(s), "local", None
    text = excerpt(s)
    if not text:
        return local_summary(s), "local", "no transcript text"
    runner = _run_claude if engine == "claude" else _run_codex
    out, why = runner(rt, text, timeout)
    if not out:
        return local_summary(s), "local", why
    out, _ = redact(" ".join(out.split()))
    return clean(out, 240), engine, None
