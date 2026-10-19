import json
import unittest
from unittest import mock

from helpers import NOW, Home

from chainlog import config as config_mod
from chainlog import setup as setup_mod
from chainlog import sweep
from chainlog.index import load
from chainlog.integrations import base
from chainlog.integrations import components as comp
from chainlog.store import spool

SID = "aaaa1111-0000-0000-0000-000000000001"


class Policy(Home):
    def configure(self, action, engine="local", installed=NOW - 86400):
        rt = self.make_rt()
        cfg = config_mod.load(rt)
        cfg = config_mod.set_value(cfg, "unsaved.action", action)
        cfg = config_mod.set_value(cfg, "summary.engine", engine)
        cfg["installed_at"] = installed
        config_mod.save(rt, cfg)
        return cfg

    def ended_session(self, ended_ago=700, sid=SID, pr=None):
        self.claude_session(sid, NOW - 4000, NOW - ended_ago, title="Shop PR 1234 refund", pr=pr)
        self.spool_end("claude", sid, NOW - ended_ago, anc=[5001, 4242])

    def test_summarize_once_with_chain(self):
        cfg = self.configure("summarize")
        self.ended_session(pr=1234)
        rt = self.make_rt()
        sweep.run(load(rt, cfg), mode="cron")
        sweep.run(load(rt, cfg), mode="cron")  # second run must not act again
        s = load(rt, cfg).sessions["claude:" + SID]
        self.assertEqual(s["saved"], "auto")
        self.assertEqual(len([n for n in s["notes"] if n["kind"] == "summary"]), 1)
        self.assertIn("Shop PR 1234 refund", s["summary"]["text"])
        self.assertEqual(s["chains"][0]["id"], "shop#1234")

    def test_not_due_yet_sets_due_file(self):
        cfg = self.configure("summarize")
        self.ended_session(ended_ago=120)
        rt = self.make_rt()
        sweep.run(load(rt, cfg), mode="cron")
        s = load(rt, cfg).sessions["claude:" + SID]
        self.assertEqual(s["saved"], "none")
        self.assertEqual(spool.pending_due(rt), int(NOW - 120 + 600))

    def test_sessions_before_install_untouched(self):
        cfg = self.configure("summarize", installed=NOW - 60)
        self.ended_session()
        rt = self.make_rt()
        sweep.run(load(rt, cfg), mode="cron")
        self.assertEqual(load(rt, cfg).sessions["claude:" + SID]["saved"], "none")

    def test_list_policy_prints_previews_in_tty_once(self):
        cfg = self.configure("list")
        self.ended_session()
        rt = self.make_rt()
        lines = sweep.run(load(rt, cfg), mode="tty")
        self.assertTrue(lines[0].startswith("chainlog ▸ 1 unsaved session"))
        self.assertTrue(any("last work" in l for l in lines))
        self.assertEqual(sweep.run(load(rt, cfg), mode="tty"), [])
        self.assertEqual(load(rt, cfg).sessions["claude:" + SID]["saved"], "none")  # list never saves

    def test_remind_uses_argv_osascript(self):
        cfg = self.configure("remind")
        self.ended_session()
        rt = self.make_rt()
        self.recorder.responses[("osascript",)] = (0, "", "")
        with mock.patch("chainlog.notify.sys.platform", "darwin"):  # the macOS path, on any CI runner
            sweep.run(load(rt, cfg), mode="cron")
        calls = [c for c in self.recorder.calls if c[0] == "osascript"]
        self.assertTrue(calls, "osascript was not called")
        self.assertIn("chainlog: unsaved session", calls[0])  # passed as argv, not AppleScript source
        self.assertEqual(load(rt, cfg).sessions["claude:" + SID]["unsaved_handled"]["action"], "remind")

    def test_engine_failure_falls_back_to_local(self):
        cfg = self.configure("summarize", engine="claude")
        self.ended_session()
        rt = self.make_rt()
        self.recorder.responses[("claude",)] = (1, "", "auth error")
        sweep.run(load(rt, cfg), mode="cron")
        evs = [e for e in load(rt, cfg).events if e["type"] == "save"]
        self.assertEqual(evs[0]["data"]["summary_engine"], "local")
        self.assertIn("claude exited 1", evs[0]["data"]["fallback_reason"])
        claude_call = [c for c in self.recorder.calls if c[0] == "claude"][0]
        self.assertIn("--no-session-persistence", claude_call)
        self.assertNotIn("--bare", claude_call)


HERDR_HOOKS = """{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "command": "bash '/x/.codex/herdr-agent-state.sh' session",
            "timeout": 10,
            "type": "command"
          }
        ]
      }
    ]
  }
}"""


class SetupRoundTrip(Home):
    def setUp(self):
        super().setUp()
        (self.home / ".codex" / "hooks.json").write_text(HERDR_HOOKS)
        (self.home / ".zshrc").write_text("export PATH=$HOME/bin:$PATH\n")

    def test_install_uninstall_byte_identical(self):
        rt = self.make_rt()
        before_hooks = (self.home / ".codex" / "hooks.json").read_bytes()
        before_rc = (self.home / ".zshrc").read_bytes()
        changes = comp.plan_codex(rt) + comp.plan_shell(rt)
        base.apply(rt, changes)
        hooks = json.loads((self.home / ".codex" / "hooks.json").read_text())
        self.assertEqual(hooks["hooks"]["SessionStart"], json.loads(HERDR_HOOKS)["hooks"]["SessionStart"])
        self.assertIn(comp.HOOK_MARK, json.dumps(hooks["hooks"]["SessionEnd"]))
        self.assertIn(comp.MARK_BEGIN, (self.home / ".zshrc").read_text())
        self.assertTrue(all(c.noop for c in comp.plan_codex(rt)))  # idempotent

        class A:
            purge = False
            yes = True
        setup_mod.uninstall(rt, A(), lambda *a: None)
        self.assertEqual((self.home / ".codex" / "hooks.json").read_bytes(), before_hooks)
        self.assertEqual((self.home / ".zshrc").read_bytes(), before_rc)
        self.assertFalse((self.home / ".codex" / "skills" / "chainlog-save").joinpath("SKILL.md").exists())

    def test_uninstall_after_herdr_rewrite_removes_only_ours(self):
        rt = self.make_rt()
        base.apply(rt, comp.plan_codex(rt))
        data = json.loads((self.home / ".codex" / "hooks.json").read_text())
        data["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "herdr-new"}]}]  # someone else edits
        (self.home / ".codex" / "hooks.json").write_text(json.dumps(data, indent=2))

        class A:
            purge = False
            yes = True
        setup_mod.uninstall(rt, A(), lambda *a: None)
        after = json.loads((self.home / ".codex" / "hooks.json").read_text())
        self.assertNotIn("SessionEnd", after["hooks"])
        self.assertIn("Stop", after["hooks"])
        self.assertIn("SessionStart", after["hooks"])

    def test_refuses_invalid_hooks_json(self):
        (self.home / ".codex" / "hooks.json").write_text("{not json")
        with self.assertRaises(RuntimeError):
            comp.plan_codex(self.make_rt())


if __name__ == "__main__":
    unittest.main()
