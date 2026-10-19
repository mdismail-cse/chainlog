"""`chainlog` (and find / chain) in a real terminal: type a number to open that session."""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import NOW, Home

from chainlog import cli

A = "aaaa1111-0000-0000-0000-000000000001"
B = "bbbb2222-0000-0000-0000-000000000002"


class PickFromList(Home):
    def setUp(self):
        super().setUp()
        self.claude_session(A, NOW - 9000, NOW - 8000, title="Shop PR 4321 refund", pr=4321)
        self.claude_session(B, NOW - 3000, NOW - 2000, title="Tracker CSV export")

    def run_cli(self, argv, answers, tty=True):
        rt = self.make_rt(tty=tty)
        opened = []
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=list(answers)) as inp, \
                mock.patch("chainlog.resume.execute", side_effect=lambda p: opened.append(p)), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(argv, rt=rt)
        self.prompts = [c.args[0] if c.args else "" for c in inp.call_args_list]
        return code, out.getvalue(), opened, inp.call_count

    def test_number_opens_that_row(self):
        code, out, opened, _ = self.run_cli([], ["2"])  # newest first: @1 = B, @2 = A
        self.assertEqual(code, 0)
        self.assertEqual(opened[0]["argv"], ["claude", "--resume", A])
        self.assertIn("Type a number to open it", self.prompts[0])

    def test_words_search_then_pick(self):
        code, out, opened, _ = self.run_cli([], ["4321", "1"])
        self.assertEqual(opened[0]["argv"], ["claude", "--resume", A])

    def test_enter_quits_without_opening(self):
        code, out, opened, _ = self.run_cli(["find", "tracker"], [""])
        self.assertEqual((code, opened), (0, []))

    def test_chain_rounds_can_be_opened(self):
        rt = self.make_rt()
        cli.main(["save", "--id", A[:8], "r1"], rt=rt)
        code, out, opened, _ = self.run_cli(["chain", "4321"], ["1"])
        self.assertEqual(opened[0]["argv"], ["claude", "--resume", A])

    def test_scripts_are_never_asked(self):
        code, out, opened, asked = self.run_cli([], [], tty=False)
        self.assertEqual((asked, opened), (0, []))
        self.assertIn("chainlog resume @N", out)


if __name__ == "__main__":
    unittest.main()
