"""Friendlier behaviour: refuse an empty save, suggest the command you meant, tip when Claude Code
is not connected, and `review --auto` for a whole backlog."""
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import NOW, Home

from chainlog import chain as chain_mod
from chainlog import cli
from chainlog import config as config_mod
from chainlog.index import load
from chainlog.store.events import read_events

A = "aaaa1111-0000-0000-0000-000000000001"
B = "bbbb2222-0000-0000-0000-000000000002"


class UxHome(Home):
    def run_cli(self, argv, rt=None, answers=()):
        rt = rt or self.make_rt()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", side_effect=list(answers)) as inp, \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, rt=rt)
        return code, out.getvalue(), err.getvalue(), inp.call_count

    def saves(self, rt):
        return [e for e in read_events(rt) if e["type"] == "save"]


class EmptySave(UxHome):
    def setUp(self):
        super().setUp()
        self.claude_session(A, NOW - 900, NOW - 600, title="Shop PR 4321 refund", pr=4321)

    def test_no_note_is_refused_with_an_example(self):
        rt = self.make_rt()
        code, _out, err, _n = self.run_cli(["save", "--id", A[:8]], rt)
        self.assertEqual(code, 1)
        self.assertIn('chainlog save "what you did"', err)
        self.assertEqual(self.saves(rt), [])

    def test_a_status_alone_is_enough(self):
        rt = self.make_rt()
        code, _out, _err, _n = self.run_cli(["save", "--id", A[:8], "-s", "pass"], rt)
        self.assertEqual(code, 0)
        self.assertEqual(self.saves(rt)[0]["data"]["status"], "pass")

    def test_blank_stdin_from_a_skill_is_refused_too(self):
        rt = self.make_rt()
        rt.stdin = io.StringIO("   \n")
        code, _out, err, _n = self.run_cli(["save", "--id", A[:8], "--args-stdin"], rt)
        self.assertEqual(code, 1)
        self.assertIn("nothing to save", err)


class DidYouMean(UxHome):
    def setUp(self):
        super().setUp()
        self.claude_session(A, NOW - 900, NOW - 600, title="Shop PR 4321 refund", pr=4321)

    def test_typo_with_no_hits_says_what_you_meant(self):
        code, out, _err, _n = self.run_cli(["svae", "zzz", "qqq"])
        self.assertEqual(code, 1)
        self.assertIn('did you mean:  chainlog save "zzz qqq"', out)

    def test_typo_that_still_finds_sessions_hints_on_stderr(self):
        code, out, err, _n = self.run_cli(["svae", "r2", "refund"])
        self.assertEqual(code, 0)  # the refund session is listed
        self.assertIn("refund", out)
        self.assertIn('chainlog save "r2 refund"', err)

    def test_other_command_names_keep_their_arguments(self):
        _code, out, _err, _n = self.run_cli(["shwo", "@1"])
        self.assertIn("did you mean:  chainlog show @1", out)

    def test_ordinary_searches_get_no_guess(self):
        for words in (["checkout"], ["4999"], ["refund", "flow", "zzz"]):
            _code, out, _err, _n = self.run_cli(words)
            self.assertNotIn("did you mean", out, words)

    def test_a_real_match_is_never_second_guessed(self):
        code, out, err, _n = self.run_cli(["refund"])
        self.assertEqual(code, 0)
        self.assertNotIn("did you mean", out + err)


class ConnectTip(UxHome):
    def setUp(self):
        super().setUp()
        self.claude_session(A, NOW - 900, NOW - 600, title="Shop PR 4321 refund", pr=4321)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        claude = self.bin / "claude"
        claude.write_text("#!/bin/sh\n")
        claude.chmod(0o755)

    def plugin_installed(self):
        d = self.home / ".claude" / "plugins"
        d.mkdir(parents=True)
        (d / "installed_plugins.json").write_text(json.dumps({"plugins": {"chainlog@chainlog": [{"scope": "user"}]}}))

    def list_view(self, tty=True, env=None):
        rt = self.make_rt(tty=tty, env=dict({"PATH": str(self.bin)}, **(env or {})))
        return self.run_cli([], rt, answers=[""])[1]

    def test_tip_names_the_one_command_that_fixes_it(self):
        out = self.list_view()
        self.assertIn("tip:", out)
        self.assertIn("chainlog setup claude", out)

    def test_no_tip_once_connected(self):
        self.plugin_installed()
        self.assertNotIn("tip:", self.list_view())

    def test_no_tip_for_scripts_and_agents(self):
        self.assertNotIn("tip:", self.list_view(tty=False))

    def test_no_tip_when_claude_is_not_installed(self):
        (self.bin / "claude").unlink()
        self.assertNotIn("tip:", self.list_view())

    def test_hints_off_silences_it(self):
        rt = self.make_rt()
        self.run_cli(["config", "set", "hints", "off"], rt)
        self.assertNotIn("tip:", self.list_view())


if __name__ == "__main__":
    unittest.main()
