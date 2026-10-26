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


if __name__ == "__main__":
    unittest.main()
