import json
import os
import subprocess
import threading
import unittest

from helpers import NOW, ROOT, Home

from chainlog import config as config_mod
from chainlog.chain import normalize_key, parse_ref
from chainlog.index import load, write_events
from chainlog.noteargs import parse
from chainlog.paths import SafetyError, safe_open
from chainlog.redact import redact
from chainlog.resolve import resolve_ref, which
from chainlog.store.events import new_event, read_events


class NoteArgs(unittest.TestCase):
    def test_flags_at_edges_only(self):
        p = parse("r2 — refund 500 on -s partial amounts -s fail")
        self.assertEqual(p["status"], "fail")
        self.assertEqual(p["note"], "r2 — refund 500 on -s partial amounts")

    def test_leading_flags_and_chain(self):
        p = parse("-c Shop#4321 -t smoke step 3 blocked --no-chain")
        self.assertEqual((p["chain"], p["tags"], p["no_chain"]), ("Shop#4321", ["smoke"], True))
        self.assertEqual(p["note"], "step 3 blocked")

    def test_quotes_and_dollar_are_data(self):
        p = parse("it's \"quoted\" $HOME $(touch /tmp/x)")
        self.assertEqual(p["note"], "it's \"quoted\" $HOME $(touch /tmp/x)")

    def test_double_dash_disables_flags(self):
        self.assertEqual(parse("-- -s is literal")["note"], "-s is literal")

    def test_empty(self):
        self.assertEqual(parse("")["note"], "")


class Redaction(unittest.TestCase):
    def test_tokens(self):
        text, n = redact("key sk-ant-abcdefghijklmnop and ghp_" + "a" * 36 + " password=hunter2 https://u:p@host/x")
        self.assertNotIn("hunter2", text)
        self.assertNotIn("sk-ant-abc", text)
        self.assertNotIn("u:p@", text)
        self.assertEqual(n, 4)


class Chains(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(normalize_key("Shop#4321"), "shop#4321")
        self.assertEqual(parse_ref("shop#4321/2"), ("shop", 4321, 2))
        self.assertEqual(parse_ref("4321"), (None, 4321, None))
        self.assertIsNone(parse_ref("refund"))


class Safety(Home):
    def test_denylist(self):
        rt = self.make_rt()
        key = self.home / ".claude" / "sessions" / "1.abc.key"
        key.parent.mkdir(parents=True)
        key.write_text("secret")
        with self.assertRaises(SafetyError):
            safe_open(key)
        with self.assertRaises(SafetyError):
            safe_open(self.home / ".codex" / "auth.json")

    def test_no_harness_writes(self):
        rt = self.make_rt()
        with self.assertRaises(SafetyError):
            safe_open(self.home / ".claude" / "x.json", "w", rt=rt)


class Sources(Home):
    def test_index_titles_children_and_injected_context(self):
        self.claude_session("aaaa1111-0000-0000-0000-000000000001", NOW - 7200, NOW - 3600, title="Shop PR 4321 refund")
        self.claude_session("bbbb2222-0000-0000-0000-000000000002", NOW - 7200, NOW - 3600, subagent=True)
        self.codex_session("01a1aaaa-0000-0000-0000-000000000003", NOW - 7000, NOW - 3000, name="Deep test PR 4321 Plan A")
        self.codex_session("01a1bbbb-0000-0000-0000-000000000004", NOW - 7000, NOW - 3000,
                           parent="01a1aaaa-0000-0000-0000-000000000003")
        idx = load(self.make_rt())
        vis = idx.visible()
        self.assertEqual(sorted(s["harness"] for s in vis), ["claude", "codex"])
        codex = [s for s in vis if s["harness"] == "codex"][0]
        self.assertEqual(codex["title"]["value"], "Deep test PR 4321 Plan A")
        self.assertEqual(codex["last_ask"], "check staging")  # <environment_context> filtered
        claude = [s for s in vis if s["harness"] == "claude"][0]
        self.assertEqual(claude["last_work"], "Refund still returns 500")
        self.assertEqual(claude["git"]["remote"], "git@github.com:acme/Shop.git")


class SaveAndFold(Home):
    def test_save_undo_and_rounds(self):
        a = "aaaa1111-0000-0000-0000-000000000001"
        b = "cccc3333-0000-0000-0000-000000000003"
        self.claude_session(a, NOW - 86400 * 2, NOW - 86400 * 2 + 600, title="Shop PR 4321 checkout", pr=4321)
        self.claude_session(b, NOW - 86400, NOW - 86400 + 600, title="Shop PR 4321 refund", pr=4321)
        rt = self.make_rt()
        write_events(rt, [new_event(rt, "save", "claude:" + b, "cli", {"note": "r2", "chain": "shop#4321"})])
        write_events(rt, [new_event(rt, "save", "claude:" + a, "cli", {"note": "r1", "chain": "shop#4321"})])
        idx = load(rt)
        members = idx.chains["shop#4321"]["members"]
        self.assertEqual([m["ref"] for m in members], ["claude:" + a, "claude:" + b])  # by start time
        self.assertEqual(resolve_ref(idx, "4321")[0]["ref"], "claude:" + b)
        self.assertEqual(resolve_ref(idx, "shop#4321/1")[0]["ref"], "claude:" + a)
        # undo the last save -> chain loses round "a"
        last = read_events(rt)[-1]
        write_events(rt, [new_event(rt, "undo", last["ref"], "cli", {"event": last["id"]})])
        idx = load(rt)
        self.assertEqual(len(idx.chains["shop#4321"]["members"]), 1)
        self.assertEqual(idx.sessions["claude:" + a]["saved"], "none")

    def test_concurrent_writes_and_idempotent_ingest(self):
        self.claude_session("aaaa1111-0000-0000-0000-000000000001", NOW - 600, NOW - 60)
        rt = self.make_rt()
        for i in range(10):
            self.spool_end("claude", "aaaa1111-0000-0000-0000-000000000001", NOW - 60 + i, [9000 + i])
        threads = [threading.Thread(target=lambda i=i: write_events(
            rt, [new_event(rt, "note", "claude:aaaa1111-0000-0000-0000-000000000001", "cli", {"text": str(i)})]))
            for i in range(50)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        load(rt)
        load(rt)  # re-ingest must add nothing
        self.assertEqual(len(read_events(rt)), 60)


class Resolver(Home):
    sid = "aaaa1111-0000-0000-0000-000000000001"

    def test_after_exit_same_shell_is_exact(self):
        self.claude_session(self.sid, NOW - 3000, NOW - 40)
        self.spool_end("claude", self.sid, NOW - 40, anc=[5001, 4242, 100])  # claude ran under shell 4242
        kind, c = which(load(self.make_rt(ppid=4242)))
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("exact", self.sid))

    def test_same_terminal_tab_is_strong(self):
        self.claude_session(self.sid, NOW - 3000, NOW - 40)
        self.spool_end("claude", self.sid, NOW - 40, anc=[5001, 7777], fp={"term": "w0t1p0:ABC"})
        kind, c = which(load(self.make_rt(ppid=4242, env={"TERM_SESSION_ID": "w0t1p0:ABC"})))
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("strong", self.sid))

    def test_parallel_panes_do_not_cross(self):
        other = "dddd4444-0000-0000-0000-000000000004"
        self.claude_session(self.sid, NOW - 3000, NOW - 100)
        self.claude_session(other, NOW - 3000, NOW - 20)
        self.spool_end("claude", self.sid, NOW - 100, anc=[5001, 4242], fp={"herdr_pane": "w5:p1"})
        self.spool_end("claude", other, NOW - 20, anc=[6001, 8888], fp={"herdr_pane": "w5:p2"})
        kind, c = which(load(self.make_rt(ppid=4242, env={"HERDR_PANE_ID": "w5:p1"})))
        self.assertEqual(c[0]["session"]["native_id"], self.sid)  # not the newer one in pane p2

    def test_folder_only_single_is_weak_two_is_many(self):
        self.claude_session(self.sid, NOW - 3000, NOW - 120)
        rt = self.make_rt(ppid=4242)
        kind, c = which(load(rt))
        self.assertEqual(kind, "weak")
        self.assertIn("guessed by folder", c[0]["reason"])
        self.codex_session("01a1aaaa-0000-0000-0000-000000000003", NOW - 3000, NOW - 60)
        kind, c = which(load(rt))
        self.assertEqual(kind, "many")
        self.assertEqual(c[0]["session"]["harness"], "codex")  # most recent first

    def test_inside_session_env(self):
        rt = self.make_rt(ppid=4242, proctable={4242: (3000, "-zsh"), 3000: (1, "claude")},
                          env={"CLAUDE_CODE_SESSION_ID": self.sid})
        kind, c = which(load(rt))
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("exact", self.sid))


class SpoolContract(Home):
    def run_stub(self, stdin, env=None, args=("claude", "end")):
        e = dict(os.environ, CHAINLOG_HOME=str(self.data), HOME=str(self.home))
        e.update(env or {})
        return subprocess.run(["sh", str(ROOT / "plugin" / "hooks" / "spool.sh")] + list(args),
                              input=stdin, capture_output=True, env=e)

    def test_silent_exit0_and_ingest(self):
        sid = "aaaa1111-0000-0000-0000-000000000001"
        p = self.run_stub(json.dumps({"session_id": sid, "reason": "prompt_input_exit"}).encode(),
                          env={"HERDR_PANE_ID": 'w5:p1"$(x)', "CHAINLOG_CHAIN": "shop#4321"})
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))
        idx = load(self.make_rt())
        s = idx.sessions["claude:" + sid]
        self.assertEqual(s["end"]["reason"], "prompt_input_exit")
        self.assertEqual(s["hooks"][0]["fp"]["herdr_pane"], "w5:p1x")  # sanitised
        self.assertEqual(s["hooks"][0]["chain"], "shop#4321")
        self.assertTrue((self.data / "spool" / ".due").exists())

    def test_truncated_stdin_keeps_fingerprint(self):
        p = self.run_stub(b'{"session_id":"aaaa1111-0000-0000-0000-000000000001","transcript_pa')
        self.assertEqual(p.returncode, 0)
        idx = load(self.make_rt())
        self.assertIn("claude:aaaa1111-0000-0000-0000-000000000001", idx.sessions)

    def test_summarizer_guard(self):
        self.run_stub(b"{}", env={"CHAINLOG_SUMMARIZER": "1"})
        self.assertFalse((self.data / "spool").exists())

    def test_readonly_home(self):
        self.data.mkdir()
        os.chmod(self.data, 0o500)
        try:
            p = self.run_stub(b'{"session_id":"x"}')
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))
        finally:
            os.chmod(self.data, 0o700)


if __name__ == "__main__":
    unittest.main()
