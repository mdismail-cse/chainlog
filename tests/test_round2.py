"""Regression tests for the round-2 simulation findings."""
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import NOW, Home

from chainlog import cli
from chainlog import setup as setup_mod
from chainlog.index import load, write_events
from chainlog.integrations import base
from chainlog.integrations import components as comp
from chainlog.resolve import which
from chainlog.store.events import new_event

A = "aaaa1111-0000-0000-0000-000000000001"
B = "bbbb2222-0000-0000-0000-000000000002"
CDX = "01a1cccc-0000-0000-0000-000000000003"

HERDR = ('{\n  "hooks": {\n    "SessionStart": [\n      {\n        "hooks": [\n          {\n'
         '            "command": "bash \'/x/.codex/herdr-agent-state.sh\' session",\n            "timeout": 10,\n'
         '            "type": "command"\n          }\n        ]\n      }\n    ]\n  }\n}')


def run_cli(rt, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv, rt=rt)
    return code, out.getvalue(), err.getvalue()


class Args:
    def __init__(self, **kw):
        self.__dict__.update(dict(purge=False, yes=True, components=[], dry_run=False, skip=[], policy=None,
                                  engine=None, local=False, json=False))
        self.__dict__.update(kw)


class Matcher(Home):
    def test_parallel_codex_in_same_folder_does_not_force_a_question(self):
        self.codex_session(CDX, NOW - 5000, NOW - 10)  # running in another pane since before A ended
        self.claude_session(A, NOW - 3000, NOW - 120)
        self.spool_end("claude", A, NOW - 120, anc=[11, 4242])
        kind, c = which(load(self.make_rt(ppid=4242)))
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("exact", A))

    def test_empty_successor_does_not_force_a_question(self):
        self.claude_session(A, NOW - 3000, NOW - 300)
        self.spool_end("claude", A, NOW - 300, anc=[11, 4242])
        self.spool_end("claude", B, NOW - 60, anc=[12, 4242], event="start")  # opened, nothing typed, killed
        kind, c = which(load(self.make_rt(ppid=4242)))
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("exact", A))

    def test_same_tab_different_panes_asks(self):
        self.claude_session(A, NOW - 3000, NOW - 200)
        self.claude_session(B, NOW - 3000, NOW - 100)
        self.spool_end("claude", A, NOW - 200, anc=[11], fp={"term": "TAB", "herdr_pane": "w5:p1"})
        self.spool_end("claude", B, NOW - 100, anc=[12], fp={"term": "TAB", "herdr_pane": "w5:p2"})
        kind, _c = which(load(self.make_rt(env={"TERM_SESSION_ID": "TAB"})))  # no pane id here
        self.assertEqual(kind, "many")

    def test_codex_subagent_thread_saves_to_parent(self):
        self.codex_session(CDX, NOW - 3000, NOW - 10)
        child = "01a1dddd-0000-0000-0000-000000000004"
        self.codex_session(child, NOW - 2000, NOW - 20, parent=CDX)
        rt = self.make_rt(env={"CODEX_THREAD_ID": child}, proctable={4242: (3000, "bash"), 3000: (1, "codex")})
        kind, c = which(load(rt), via="skill:codex")
        self.assertEqual(c[0]["session"]["native_id"], CDX)


class Chains(Home):
    def test_save_reports_and_replaces_the_auto_chain(self):
        self.claude_session(A, NOW - 3000, NOW - 2000, title="Shop PR 4321", pr=4321)
        rt = self.make_rt()
        run_cli(rt, ["save", "--id", A[:8], "r1"])  # auto-linked shop#4321
        code, out, _e = run_cli(rt, ["save", "--id", A[:8], "actually 6000", "-c", "6000"])
        self.assertIn("shop#6000", out)
        self.assertEqual([c["id"] for c in load(rt).sessions["claude:" + A]["chains"]], ["shop#6000"])

    def test_round_suffix_and_ambiguous_bare_number(self):
        loose = self.root / "work" / "loose"
        loose.mkdir(parents=True)
        self.claude_session(A, NOW - 3000, NOW - 2000, cwd=loose)  # no git remote
        rt = self.make_rt()
        for key in ("shop#4321", "tracker#4321"):
            write_events(rt, [new_event(rt, "chain.link", "claude:" + B, "cli", {"chain": key})])
        code, _o, err = run_cli(rt, ["save", "--id", A[:8], "x", "-c", "4321"])
        self.assertEqual(code, 1)
        self.assertIn("several chains", err)
        run_cli(rt, ["save", "--id", A[:8], "x", "-c", "shop#4321/2"])
        self.assertIn("shop#4321", [c["id"] for c in load(rt).sessions["claude:" + A]["chains"]])
        self.assertNotIn("shop#4321/2", load(rt).chains)

    def test_ls_pr_respects_the_repo(self):
        self.claude_session(A, NOW - 3000, NOW - 2000, title="Shop PR 4321", pr=4321)
        rt = self.make_rt()
        _c, out, _e = run_cli(rt, ["ls", "--pr", "tracker#4321", "--json"])
        self.assertEqual(json.loads(out), [])
        _c, out, _e = run_cli(rt, ["ls", "--pr", "shop#4321", "--json"])
        self.assertEqual(len(json.loads(out)), 1)


class Robustness(Home):
    def test_bad_field_types_never_crash(self):
        self.claude_session(A, NOW - 3000, NOW - 2000, pr=4321)
        p = next((self.home / ".claude" / "projects").rglob(A + ".jsonl"))
        with open(p, "a") as f:
            f.write(json.dumps({"type": "pr-link", "prNumber": "abc", "sessionId": A}) + "\n")
        (self.home / ".codex" / "session_index.jsonl").write_text(json.dumps({"id": CDX, "thread_name": 7}) + "\n")
        rt = self.make_rt()
        self.data.mkdir(parents=True, exist_ok=True)
        with open(self.data / "events.jsonl", "a") as f:
            f.write(json.dumps({"v": 1, "id": "x1", "ts": "soon", "type": "save", "ref": "claude:" + A}) + "\n")
            f.write(json.dumps({"v": 1, "id": "x2", "ts": 1, "type": "tag", "ref": "claude:" + A,
                                "data": {"add": 5}}) + "\n")
        for argv in (["ls"], ["ls", "--pr", "4321"], ["show", A[:8]], ["doctor"]):
            code, _o, err = run_cli(rt, argv)
            self.assertNotIn("Traceback", err, argv)

    def test_restored_transcript_is_resumable_again(self):
        p = self.claude_session(A, NOW - 3000, NOW - 2000)
        rt = self.make_rt()
        run_cli(rt, ["save", "--id", A[:8], "r1"])
        tmp = self.root / "aside.jsonl"
        shutil.move(str(p), str(tmp))
        self.assertFalse(load(rt).sessions["claude:" + A]["transcript"]["present"])
        shutil.move(str(tmp), str(p))  # same inode, size and mtime
        self.assertTrue(load(rt).sessions["claude:" + A]["transcript"]["present"])


class SetupBytes(Home):
    def test_crlf_and_herdr_bytes_survive(self):
        (self.home / ".zshrc").write_bytes(b"export A=1\r\nexport B=2\r\n")
        (self.home / ".codex" / "hooks.json").write_text(HERDR)
        rt = self.make_rt()
        base.apply(rt, comp.plan_shell(rt) + comp.plan_codex(rt))
        after = (self.home / ".codex" / "hooks.json").read_text()
        self.assertTrue(after.startswith(HERDR[: HERDR.rindex("]") + 1]))  # herdr's lines untouched
        self.assertTrue((self.home / ".zshrc").read_bytes().startswith(b"export A=1\r\nexport B=2\r\n"))
        setup_mod.uninstall(rt, Args(), lambda *a: None)
        self.assertEqual((self.home / ".zshrc").read_bytes(), b"export A=1\r\nexport B=2\r\n")
        self.assertEqual((self.home / ".codex" / "hooks.json").read_text(), HERDR)

    def test_requested_part_that_cannot_be_set_up_fails(self):
        (self.home / ".codex" / "hooks.json").write_text("{broken")
        code, _o, _e = run_cli(self.make_rt(), ["setup", "codex", "--yes"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
