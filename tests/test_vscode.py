"""Sessions started in the Claude Code VS Code extension: imported once, tagged, filterable, and reopened in it."""
import json
import unittest
from unittest import mock

from helpers import NOW
from test_menubar import ID, MenuData, H

from chainlog import config as config_mod
from chainlog import resume
from chainlog.index import load


class Origin(unittest.TestCase):
    def test_where_a_session_was_started(self):
        for entrypoint, expect in (("claude-vscode", ("vscode", "VS Code")), ("vscode", ("vscode", "VS Code")),
                                   ("claude-desktop", ("desktop", "Desktop")), ("cli", ("terminal", None)),
                                   ("sdk-cli", (None, None)), (None, (None, None))):
            self.assertEqual(resume.origin({"entrypoint": entrypoint}), expect, entrypoint)


class VsCodeSessions(MenuData):
    def setUp(self):
        super().setUp()
        self.claude_session(ID[0], NOW - 5 * H, NOW - 4 * H, title="From the extension", entrypoint="claude-vscode")
        self.claude_session(ID[1], NOW - 3 * H, NOW - 2 * H, title="From the terminal", entrypoint="cli")
        self.claude_session(ID[2], NOW - 2 * H, NOW - 1 * H, title="From the desktop app", entrypoint="claude-desktop")

    def index(self):
        return load(self.rt, config_mod.load(self.rt))

    def session(self, i):
        return next(s for s in self.index().visible() if s["native_id"] == ID[i])

    def rows(self):
        d = self.build()
        return {r["title"]: r for r in d["unsaved"] + d["recent"]}

    def test_each_session_is_imported_once_and_stays_one_after_more_scans(self):
        self.assertEqual(sorted(s["native_id"] for s in self.index().visible()), sorted(ID[:3]))
        path = next((self.home / ".claude" / "projects").rglob(ID[0] + ".jsonl"))
        with open(path, "a") as f:  # the session keeps going in the extension; the next scan must update it, not add a copy
            f.write(json.dumps({"type": "last-prompt", "lastPrompt": "and then", "sessionId": ID[0]}) + "\n")
        self.index()
        self.assertEqual(sorted(s["native_id"] for s in self.index().visible()), sorted(ID[:3]))

    def test_rows_say_where_the_session_was_started(self):
        rows = self.rows()
        self.assertEqual(rows["From the extension"]["via"], "VS Code")
        self.assertEqual(rows["From the desktop app"]["via"], "Desktop")
        self.assertIsNone(rows["From the terminal"]["via"])  # a plain terminal session carries no tag

    def test_only_an_extension_session_gets_the_reopen_in_vscode_command(self):
        rows = self.rows()
        self.assertIsNone(rows["From the terminal"]["resume"]["vscode"])
        self.assertIsNone(rows["From the desktop app"]["resume"]["vscode"])
        with mock.patch("sys.platform", "darwin"):
            cmd = self.rows()["From the extension"]["resume"]["vscode"]
        self.assertEqual(cmd, "open -a 'Visual Studio Code' %s && sleep 2 && open 'vscode://anthropic.claude-code/open?session=%s'"
                         % (self.proj, ID[0]))

    def test_the_command_is_refused_where_it_cannot_work(self):
        s = self.session(0)
        ok = {"blockers": [], "cwd": s["cwd"]}
        self.assertTrue(resume.vscode_command(s, ok, platform="darwin"))
        self.assertEqual(resume.vscode_command(s, ok, platform="linux"), "")  # `open -a` is macOS
        self.assertEqual(resume.vscode_command(s, dict(ok, blockers=["the transcript file is gone"]), platform="darwin"), "")
        self.assertEqual(resume.vscode_command(dict(s, cwd=None), ok, platform="darwin"), "")
        self.assertEqual(resume.vscode_command(dict(s, native_id="x; rm -rf ~"), ok, platform="darwin"), "")  # goes into a link
        self.assertEqual(resume.vscode_command(dict(s, harness="codex"), ok, platform="darwin"), "")

    def test_a_folder_with_quotes_and_spaces_stays_one_argument(self):
        s = dict(self.session(0), cwd="/work/it's my folder")
        cmd = resume.vscode_command(s, {"blockers": [], "cwd": s["cwd"]}, platform="darwin")
        import shlex
        self.assertEqual(shlex.split(cmd)[:4], ["open", "-a", "Visual Studio Code", "/work/it's my folder"])

    def test_ls_via_filters_by_where_it_was_started(self):
        for via, want in (("vscode", ["From the extension"]), ("desktop", ["From the desktop app"]),
                          ("terminal", ["From the terminal"])):
            code, out, _ = self.run_cli(["ls", "--via", via, "--json"])
            self.assertEqual(code, 0)
            self.assertEqual([s["title"]["value"] for s in json.loads(out)], want, via)

    def test_typing_vscode_finds_the_extension_sessions(self):
        code, out, _ = self.run_cli(["find", "vscode", "--json"])
        titles = [s["title"]["value"] for s in json.loads(out)]
        self.assertIn("From the extension", titles)
        self.assertNotIn("From the terminal", titles)

    def test_show_says_where_it_was_started(self):
        _, out, _ = self.run_cli(["show", ID[0][:8]])
        self.assertIn("in VS Code", out)
        _, out, _ = self.run_cli(["show", ID[1][:8]])
        self.assertNotIn(" in VS Code", out)


if __name__ == "__main__":
    unittest.main()
