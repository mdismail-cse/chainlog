"""Test helpers: a fake HOME with synthetic Claude / Codex transcripts and a Runtime with a fixed
clock, a fake process table and a recording command runner. No real transcript is ever used."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "src"))

from chainlog.runtime import Runtime  # noqa: E402

NOW = 1792400000.0  # fixed "now"


def iso(t):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


class Recorder:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def __call__(self, argv, timeout=5.0, input=None, env=None, cwd=None):
        self.calls.append(list(argv))
        for prefix, resp in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return resp
        return 1, "", "not available in tests"


class Home(unittest.TestCase):
    """Base TestCase: self.home (fake HOME), self.data (CHAINLOG_HOME), self.make_rt()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="chainlog-test-")
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.data = self.root / "data"
        self.home.mkdir()
        (self.home / ".claude" / "projects").mkdir(parents=True)
        (self.home / ".codex" / "sessions").mkdir(parents=True)
        self.proj = self.root / "work" / "shop"
        (self.proj / ".git").mkdir(parents=True)
        (self.proj / ".git" / "config").write_text('[remote "origin"]\n\turl = git@github.com:acme/Shop.git\n')
        self.recorder = Recorder()

    def tearDown(self):
        self._tmp.cleanup()

    def make_rt(self, now=NOW, ppid=4242, proctable=None, env=None, cwd=None, tty=False, tty_name=""):
        e = {"HOME": str(self.home), "CHAINLOG_HOME": str(self.data), "PATH": os.environ.get("PATH", ""),
             "PWD": str(cwd or self.proj), "TMPDIR": str(self.root / "tmp")}
        e.update(env or {})
        return Runtime(env=e, home=self.home, clock=lambda: now, run=self.recorder,
                       proctable=proctable if proctable is not None else {4242: (1, "-zsh")},
                       ppid=ppid, tty=tty, tty_name=tty_name)

    # --- fixtures -------------------------------------------------------------------------------
    def claude_session(self, sid, start, end, title=None, prompt="test the refund flow",
                       work="Refund still returns 500", pr=None, cwd=None, entrypoint="cli", subagent=False):
        cwd = str(cwd or self.proj)
        d = self.home / ".claude" / "projects" / cwd.replace("/", "-")
        if subagent:
            d = d / "parent-id" / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "mode", "mode": "normal", "sessionId": sid},
            {"type": "user", "sessionId": sid, "cwd": cwd, "gitBranch": "fix/refund", "version": "2.1.303",
             "entrypoint": entrypoint, "timestamp": iso(start), "message": {"role": "user", "content": prompt}},
            {"type": "user", "sessionId": sid, "cwd": cwd, "isMeta": True, "timestamp": iso(start + 1),
             "message": {"role": "user", "content": "<system-reminder>ignore</system-reminder>"}},
            {"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": iso(end),
             "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "x"},
                                                           {"type": "text", "text": work}]}},
            {"type": "last-prompt", "lastPrompt": prompt, "sessionId": sid},
        ]
        if title:
            lines.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
        if pr:
            lines.append({"type": "pr-link", "prNumber": pr, "prRepository": "acme/Shop",
                          "prUrl": "https://github.com/acme/Shop/pull/%d" % pr, "sessionId": sid,
                          "timestamp": iso(end)})
        path = d / ("%s.jsonl" % sid)
        path.write_text("".join(json.dumps(x) + "\n" for x in lines))
        os.utime(path, (end, end))
        return path

    def codex_session(self, sid, start, end, name=None, prompt="check staging", work="Step 3 blocked",
                      parent=None, cwd=None):
        cwd = str(cwd or self.proj)
        d = self.home / ".codex" / "sessions" / "2026" / "10" / "19"
        d.mkdir(parents=True, exist_ok=True)
        meta = {"id": sid, "timestamp": iso(start), "cwd": cwd, "cli_version": "0.161.0",
                "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "cli",
                "originator": "codex-tui", "git": {"branch": "master", "repository_url": "git@github.com:acme/Shop.git"}}
        if parent:
            meta["parent_thread_id"] = parent
        lines = [
            {"timestamp": iso(start), "type": "session_meta", "payload": meta},
            {"timestamp": iso(start), "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x"}]}},
            {"timestamp": iso(start + 1), "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}},
            {"timestamp": iso(end), "type": "event_msg", "payload": {"type": "agent_message", "message": work}},
        ]
        path = d / ("rollout-2026-10-19T00-00-00-%s.jsonl" % sid)
        path.write_text("".join(json.dumps(x) + "\n" for x in lines))
        os.utime(path, (end, end))
        if name:
            with open(self.home / ".codex" / "session_index.jsonl", "a") as f:
                f.write(json.dumps({"id": sid, "thread_name": name, "updated_at": iso(end)}) + "\n")
        return path

    def spool_end(self, harness, sid, t, anc, fp=None, tty="", event="end"):
        """Write a spool pair exactly as spool.sh would."""
        d = self.data / "spool"
        d.mkdir(parents=True, exist_ok=True)
        b = "%d-%d" % (t, abs(hash((sid, t, event))) % 100000)
        (d / (b + ".in")).write_text(json.dumps({"session_id": sid, "hook_event_name": "SessionEnd",
                                                  "reason": "prompt_input_exit", "cwd": str(self.proj)}))
        f = {"herdr_pane": "", "herdr_ws": "", "herdr_tab": "", "tmux": "", "term": "", "iterm": ""}
        f.update(fp or {})
        (d / (b + ".json")).write_text(json.dumps({"v": 1, "h": harness, "e": event, "t": int(t), "anc": anc,
                                                    "tty": tty, "fp": f, "chain": ""}))
