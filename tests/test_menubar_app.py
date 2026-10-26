"""The menu bar app's buttons against the real `chainlog`, end to end (macOS with swiftc only).

The app is built once for the whole class; every test then runs its headless switches (--act, --dump)
in a throwaway HOME, exactly as the popover's buttons would.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import NOW, ROOT, Home

from chainlog import config as config_mod

H = 3600
IDS = ["%08x-7777-4000-8000-%012x" % (i, i) for i in range(12)]
LAUNCHER = str(ROOT / "plugin" / "bin" / "chainlog")


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "needs macOS and swiftc")
class AppEndToEnd(Home):
    @classmethod
    def setUpClass(cls):
        cls._build = tempfile.TemporaryDirectory(prefix="chainlog-app-")
        p = subprocess.run(["sh", str(ROOT / "plugin" / "menubar" / "build.sh")], capture_output=True, text=True,
                           env=dict(os.environ, TMPDIR=cls._build.name), timeout=600)
        assert p.returncode == 0, p.stderr
        cls.binary = p.stdout.strip().splitlines()[-1] + "/Contents/MacOS/Chainlog"

    @classmethod
    def tearDownClass(cls):
        cls._build.cleanup()

    def setUp(self):
        super().setUp()
        rt = self.make_rt()
        cfg = config_mod.load(rt)
        cfg["installed_at"] = NOW - 400 * H
        cfg["unsaved"]["lookback"] = 10 ** 8
        config_mod.save(rt, cfg)
        for i, sid in enumerate(IDS):
            self.claude_session(sid, NOW - (30 - i) * H, NOW - (29 - i) * H, title="Shop PR %d review" % (5000 + i % 3),
                                work="worked on item %d with the %s widget" % (i, "zebra" if i == 3 else "plain"))
        self.env = dict(os.environ, HOME=str(self.home), CHAINLOG_HOME=str(self.data), CHAINLOG_BIN=LAUNCHER, PWD=str(self.proj))

    def app(self, *args):
        p = subprocess.run([self.binary, *args], env=self.env, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def dump(self, *args):
        rc, out, err = self.app("--dump", *args)
        self.assertEqual(rc, 0, err)
        return out

    def ref(self, i):
        return "claude:" + IDS[i]

    def show(self, i):
        p = subprocess.run([LAUNCHER, "show", self.ref(i), "--json"], env=self.env, capture_output=True, text=True)
        return json.loads(p.stdout)

    def test_star_and_unstar_move_a_session_in_and_out_of_the_starred_tab(self):
        self.assertIn("Nothing starred yet", self.dump("--tab", "starred"))
        self.assertEqual(self.app("--act", "star", self.ref(2))[:2], (0, "ok\n"))
        starred = self.dump("--tab", "starred")
        self.assertIn("Starred · 1", starred)
        self.assertIn("★ Shop PR 5002 review", starred)
        self.assertIn("Starred 1", starred.splitlines()[0])
        self.assertIn("★ Shop PR 5002 review", self.dump())  # and it is marked in the Sessions tab too
        self.assertEqual(self.app("--act", "unstar", self.ref(2))[0], 0)
        self.assertIn("Nothing starred yet", self.dump("--tab", "starred"))

    def test_starring_an_unknown_session_fails_with_the_reason(self):
        rc, out, _ = self.app("--act", "star", "claude:deadbeef-0000-0000-0000-000000000000")
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("failed:") and "no session matches" in out, out)

    def test_save_with_a_note_and_a_result_then_search_finds_it(self):
        before = self.dump().splitlines()[2]
        self.assertEqual(before, "Unsaved · 12")
        self.assertEqual(self.app("--act", "save", self.ref(0), "pass", "checkout works with saved cards")[0], 0)
        d = self.dump()
        self.assertIn("Unsaved · 11", d)
        self.assertIn("<pass>", d)
        self.assertIn("shop#5000", self.dump("--tab", "chains"))  # the PR in its title linked it into a chain
        self.assertIn("Results · 1", self.dump("--query", "checkout works"))  # the note is searchable

    def test_a_note_that_looks_like_options_is_stored_as_text(self):
        self.assertEqual(self.app("--act", "save", self.ref(1), "-", "-s fail --help -1 degrees")[0], 0)
        j = self.show(1)
        self.assertEqual([n["text"] for n in j["notes"]], ["-s fail --help -1 degrees"])
        self.assertIsNone(j["status"])

    def test_a_result_alone_is_a_save_and_nothing_at_all_is_refused(self):
        self.assertEqual(self.app("--act", "save", self.ref(4), "fail", "")[0], 0)
        self.assertEqual(self.show(4)["status"], "fail")
        rc, out, _ = self.app("--act", "save", self.ref(5), "-", "   ")
        self.assertEqual(rc, 1)
        self.assertIn("nothing to save", out)
        self.assertEqual(self.show(5)["saved"], "none")

    def test_search_reaches_sessions_that_are_in_no_list(self):
        d = self.dump("--query", "zebra")  # only session #3's transcript says "zebra"; its title is "Shop PR 5000 review"
        self.assertIn("Results · 1", d)
        self.assertIn("Shop PR 5000 review", d)
        self.assertIn("No session matches", self.dump("--query", "nothing-matches-this-qq"))

    def test_search_in_the_starred_tab_only_shows_starred_matches(self):
        self.assertIn("Starred matching · 0", self.dump("--tab", "starred", "--query", "zebra"))
        self.app("--act", "star", self.ref(3))
        d = self.dump("--tab", "starred", "--query", "zebra")
        self.assertIn("Starred matching · 1", d)
        self.assertIn("★", d)

    def test_hostile_search_text_runs_nothing_and_never_crashes(self):
        flag = self.root / "PWNED"
        for q in ("$(touch %s)" % flag, "`touch %s`" % flag, "'; touch %s; '" % flag, '"quoted" \\ back',
                  "日本語 🔗 العربية", "-s fail", "--json", "x" * 5000, " ", "%s%d%@"):
            rc, _out, err = self.app("--dump", "--query", q)
            self.assertEqual(rc, 0, (q[:30], err))
        self.assertFalse(flag.exists())

    def test_without_chainlog_it_says_so_and_does_not_crash(self):
        env = dict(self.env, CHAINLOG_BIN="/nonexistent/chainlog", HOME=str(self.root / "nohome"), PATH="/usr/bin:/bin")
        p = subprocess.run([self.binary, "--dump"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1)
        self.assertIn("could not read", p.stderr)
        for name, text in (("notfound", "chainlog was not found"), ("newer", "newer than this app"),
                           ("failed", "Could not read"), ("loading", "Loading")):
            rc, out, _ = self.app("--dump-problem", name)
            self.assertEqual(rc, 0)
            self.assertIn(text, out)


if __name__ == "__main__":
    unittest.main()
