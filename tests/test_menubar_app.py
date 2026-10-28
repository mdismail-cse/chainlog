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
import time
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

    def copy(self, what, query, *more):
        rc, out, err = self.app("--query", query, "--copy", what, *more)
        self.assertEqual(rc, 0, err)
        return dict(line.split(": ", 1) for line in out.strip().splitlines())

    def test_after_a_search_the_copy_buttons_put_the_right_thing_on_the_clipboard(self):
        import shlex
        ext = "0000abcd-7777-4000-8000-00000000abcd"
        self.claude_session(ext, NOW - 3600, NOW - 1800, title="Quokka invoice emails", entrypoint="claude-vscode")
        got = self.copy("command", "quokka")  # the search result is the first row, as on screen
        self.assertEqual(got["copied"], "cd %s && claude --resume %s" % (shlex.quote(str(self.proj)), ext))
        self.assertEqual(got["toast"], "Command copied")
        got = self.copy("id", "quokka")
        self.assertEqual((got["copied"], got["toast"]), (ext, "Session id copied"))
        got = self.copy("link", "quokka")
        self.assertEqual((got["copied"], got["toast"]),
                         ("vscode://anthropic.claude-code/open?session=" + ext, "VS Code link copied"))

    def test_a_terminal_session_has_a_command_and_an_id_but_no_link(self):
        got = self.copy("link", "zebra")  # IDS[3], started in a terminal
        self.assertEqual((got["copied"], got["toast"]), ("-", "This one has no VS Code link"))
        self.assertEqual(self.copy("id", "zebra")["copied"], IDS[3])
        self.assertIn("claude --resume " + IDS[3], self.copy("command", "zebra")["copied"])

    def test_pick_copies_from_the_row_you_name_not_the_first(self):
        got = self.copy("id", "Shop PR", "--pick", IDS[5][:8])
        self.assertEqual(got["row"], IDS[5][:8])
        self.assertEqual(got["copied"], IDS[5])

    def test_a_kept_session_whose_original_is_gone_copies_a_command_that_restores_it_first(self):
        gone = "0000beef-7777-4000-8000-00000000beef"
        self.claude_session(gone, NOW - 7200, NOW - 3600, title="Walrus export")
        self.assertEqual(self.app("--act", "save", "claude:" + gone, "-", "saving keeps a copy")[:2], (0, "ok\n"))
        next((self.home / ".claude" / "projects").rglob(gone + ".jsonl")).unlink()  # what Claude's cleanup does
        got = self.copy("command", "walrus")
        self.assertTrue(got["copied"].startswith("chainlog restore claude:" + gone + " && cd "), got["copied"])
        self.assertTrue(got["copied"].endswith("claude --resume " + gone))

    def test_a_session_whose_transcript_is_gone_copies_no_command_but_still_its_id(self):
        gone = "0000dead-7777-4000-8000-00000000dead"
        subprocess.run([LAUNCHER, "config", "set", "keep.auto", "off"], env=self.env, capture_output=True, check=True)
        self.claude_session(gone, NOW - 7200, NOW - 3600, title="Narwhal export")
        self.assertEqual(self.app("--act", "save", "claude:" + gone, "-", "no copy is made")[:2], (0, "ok\n"))
        next((self.home / ".claude" / "projects").rglob(gone + ".jsonl")).unlink()  # what Claude's cleanup does
        got = self.copy("command", "narwhal")
        self.assertEqual(got["copied"], "-")
        self.assertIn("Can't reopen", got["toast"])
        self.assertEqual(self.copy("id", "narwhal")["copied"], gone)  # the id is still worth having

    def test_starring_an_unknown_session_fails_with_the_reason(self):
        rc, out, _ = self.app("--act", "star", "claude:deadbeef-0000-0000-0000-000000000000")
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("failed:") and "no session matches" in out, out)

    def test_save_with_a_note_and_a_result_then_search_finds_it(self):
        before = next(l for l in self.dump().splitlines() if l.startswith("Unsaved"))
        self.assertTrue(before.startswith("Unsaved · 12"), before)  # "... · last N h" follows
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

    def test_hovering_a_session_previews_its_summary_at_once(self):
        d = self.dump("--hover-first")
        line = next(l for l in d.splitlines() if l.startswith("preview:"))
        self.assertIn("— worked on item", line)  # the last thing said in the first listed session
        self.assertIn(str(self.proj), line.replace(str(self.home), "~").replace("~", str(self.home)))
        self.assertNotIn("preview:", self.dump())  # nothing hovered, nothing previewed

    def test_the_apps_own_model_checks_pass(self):
        rc, out, err = self.app("--selftest", str(ROOT / "plugin" / "menubar" / "sample.json"))
        self.assertEqual(rc, 0, out + err)
        self.assertIn("selftest passed", out)
        self.assertNotIn("FAIL", out)

    def test_a_search_that_looks_like_an_option_finds_text(self):
        self.claude_session("dddd4444-0000-4000-8000-000000000004", NOW - 2 * H, NOW - 1 * H, title="Use the --json flag",
                            work="ran everything with --json")
        for q in ("--json", "-v"):
            d = self.dump("--query", q)
            self.assertNotIn("Search is not available", d, q)
        d = self.dump("--query", "--json")
        self.assertIn("Results · 1", d)
        self.assertIn("Use the --json flag", d)

    def test_enter_on_the_chains_tab_opens_the_newest_round(self):
        for i in (0, 3, 6):  # three rounds of PR 5000
            self.assertEqual(self.app("--act", "save", self.ref(i), "pass", "round %d" % i)[0], 0)
        d = self.dump("--tab", "chains")
        self.assertIn("3 rounds", d)
        self.assertIn("enter opens: Shop PR 5000 review (r3)", d)
        self.assertEqual(d.splitlines()[-1].count("(r3)"), 1)

    def test_rows_chainlog_warns_about_are_marked_as_asking_first(self):
        sample = json.loads((ROOT / "plugin" / "menubar" / "sample.json").read_text())
        sample["recent"][0]["live"] = True
        sample["recent"][0]["resume"]["notes"] = ["this session is still running (pid 1); resuming opens a second copy."]
        f = self.root / "warn.json"
        f.write_text(json.dumps(sample))
        d = self.dump(str(f))
        self.assertIn("[running] (asks first) Shop PR 4321 refund, round 3", d)
        self.assertNotIn("(asks first)", self.dump(str(ROOT / "plugin" / "menubar" / "sample.json")))

    # --- ⚙ → "Claude keeps sessions for N days…": the app's own dialog logic against the real chainlog -----------------
    def seed_old_sessions(self):
        """Two sessions last used 40 and 100 days ago (real time: the app's chainlog runs on the real clock), and the
        setting at 400 days, so nothing is due yet. The twelve in setUp are about nine days old."""
        self.old = {}
        for age in (40, 100):
            sid = "%08x-8888-4000-8000-%012x" % (age, age)
            end = time.time() - age * 86400
            self.old[age] = self.claude_session(sid, end - 600, end, title="Work from %d days ago" % age)
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.write_text(json.dumps({"model": "opus", "cleanupPeriodDays": 400}, indent=2) + "\n")

    def days(self):
        return json.loads(self.settings.read_text())["cleanupPeriodDays"]

    def set_retention(self, typed, *flags):
        rc, out, err = self.app("--set-retention", typed, *flags)
        self.assertEqual(rc, 0, out + err)
        lines = dict(l.split(": ", 1) for l in out.splitlines() if ": " in l)
        return lines.get("asked"), lines["toast"]

    def test_the_gear_menu_shows_the_days_and_what_is_about_to_expire(self):
        self.seed_old_sessions()
        self.settings.write_text(json.dumps({"cleanupPeriodDays": 105}) + "\n")  # the 100-day-old one has 5 days left
        gear = [l for l in self.dump().splitlines() if l.startswith("gear:")]
        self.assertIn("gear: Claude keeps sessions for 105 days… | 1 expires within 14 days (1 without a kept copy)", gear)
        p = subprocess.run([LAUNCHER, "keep", "claude:" + "%08x-8888-4000-8000-%012x" % (100, 100)], env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("gear: Claude keeps sessions for 105 days… | nothing about to be lost", self.dump())  # kept: no alarm

    def test_typing_a_number_changes_the_setting_and_a_shortening_asks_first(self):
        self.seed_old_sessions()
        self.assertEqual(self.set_retention("400"), (None, "Already 400 days"))
        # 60 days would take the 100-day-old session: it asks, and "no" changes nothing
        asked, toast = self.set_retention("60", "--refuse")
        self.assertIn("1 of your sessions are older than 60 days", asked)
        self.assertIn("1 of those have no kept copy", asked)
        self.assertEqual((toast, self.days()), ("-", 400))
        asked, toast = self.set_retention("60")
        self.assertEqual((toast, self.days()), ("Claude will keep sessions 60 days", 60))
        # 20 days takes the 40-day-old one as well; keeping them first changes what the question says
        for age in (40, 100):
            p = subprocess.run([LAUNCHER, "keep", "claude:" + "%08x-8888-4000-8000-%012x" % (age, age)], env=self.env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
        asked, toast = self.set_retention("20")
        self.assertIn("2 of your sessions are older than 20 days", asked)
        self.assertIn("0 of those have no kept copy", asked)
        self.assertEqual(self.days(), 20)
        # longer never asks, and the rest of settings.json is left as it was
        asked, toast = self.set_retention("3650")
        self.assertEqual((asked, toast, self.days()), (None, "Claude will keep sessions 3650 days", 3650))
        self.assertEqual(json.loads(self.settings.read_text())["model"], "opus")

    def test_a_number_that_is_not_a_number_of_days_is_refused_in_the_app(self):
        self.seed_old_sessions()
        for typed in ("abc", "0", "-3", "3651", "2.5", ""):
            asked, toast = self.set_retention(typed)
            self.assertEqual((asked, toast), (None, "Enter a whole number of days from 1 to 3650"), repr(typed))
        self.assertEqual(self.days(), 400)

    def test_the_login_item_can_be_removed_before_the_app_is_deleted(self):
        rc, out, _ = self.app("--unregister")
        self.assertEqual(rc, 0)
        self.assertIn("login item:", out)

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
