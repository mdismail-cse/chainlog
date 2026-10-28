"""Custom retention days against sessions of many ages.

Sessions are seeded at ages from half a day to over two years. For each retention setting the test follows what a
person would live through: look at the warning, keep some sessions, change the days, let Claude's cleanup run, then
open what is left (or what was kept).

Claude Code's cleanup runs inside Claude, so `claude_cleanup` applies its rule to the fake ~/.claude: a transcript is
deleted when its modification time is earlier than (now - cleanupPeriodDays days); a file exactly on the line stays.
Everything runs in a fake home folder: no test touches the real ~/.claude."""
import io
import json
import os
import shutil
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import NOW, Home

from chainlog import cli
from chainlog import config as config_mod
from chainlog import keep, menu, resume
from chainlog.index import load

DAY = 86400
# half a day past a whole number, so no session sits exactly on a retention boundary (that has its own test)
AGES = (0.5, 3.5, 12.5, 20.5, 29.5, 30.5, 44.5, 59.5, 60.5, 89.5, 90.5, 179.5, 180.5, 364.5, 365.5, 800.5)
START = 400  # what the settings file says before a test changes it
SETTINGS = {"model": "opus", "env": {"A": "1"}, "cleanupPeriodDays": START}


def sid_for(age):
    return "%08x-0000-4000-8000-%012x" % (int(age * 10), int(age * 10))


class Seeded(Home):
    def setUp(self):
        super().setUp()
        self.paths = {}
        for age in AGES:
            end = NOW - age * DAY
            self.paths[age] = self.claude_session(sid_for(age), end - 600, end, title="Work from %s days ago" % age)
        self.original = {age: p.read_bytes() for age, p in self.paths.items()}
        self.settings_path = self.home / ".claude" / "settings.json"
        self.set_settings(SETTINGS)

    # --- helpers -----------------------------------------------------------------------------------
    def set_settings(self, data):
        self.settings_path.write_text(json.dumps(data, indent=2) + "\n")

    def days_in_settings(self):
        return json.loads(self.settings_path.read_text()).get("cleanupPeriodDays")

    def cli(self, *argv, now=NOW):
        rt = self.make_rt(now=now)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", side_effect=[]), redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(argv), rt=rt)
            except SystemExit as e:  # argparse refuses a bad number outright
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def plan(self, days):
        code, out, err = self.cli("retention", str(days), "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def index(self, now=NOW):
        rt = self.make_rt(now=now)
        return load(rt, config_mod.load(rt))

    def session(self, age, now=NOW):
        return self.index(now).sessions["claude:" + sid_for(age)]

    def keep(self, *ages):
        for age in ages:
            code, out, err = self.cli("keep", "claude:" + sid_for(age))
            self.assertEqual(code, 0, out + err)

    def assert_just_written(self, path):
        """A restored file has a fresh modification time (so Claude's cleanup clock starts again). It is written at the
        machine's real time, while these tests run on a fixed clock: check the first, then move it onto the second."""
        self.assertLess(time.time() - os.stat(path).st_mtime, 120, "the restored file should look new")
        os.utime(path, (NOW, NOW))

    def on_disk(self):
        return {age for age, p in self.paths.items() if p.exists()}

    def claude_cleanup(self, days, now=NOW):
        """Claude's rule, applied to the fake home. Returns the ages that were deleted."""
        cutoff = now - days * DAY
        gone = set()
        for age, p in self.paths.items():
            if p.exists() and os.stat(p).st_mtime < cutoff:
                p.unlink()
                folder = p.with_suffix("")
                if folder.is_dir():
                    shutil.rmtree(folder)
                gone.add(age)
        return gone


def make_case(days):
    """The whole story for one retention value."""

    def story(self):
        older = {a for a in AGES if a > days}  # what Claude will delete once it runs with this setting
        kept = set(AGES[::2])  # every other session is kept first
        shortening = days < START

        # 1. before changing anything: the plan counts exactly the sessions that would go, and those with no copy
        p = self.plan(days)
        self.assertEqual((p["current"], p["to"]), (START, days))
        self.assertEqual(p["would_delete"], len(older) if shortening else 0, p)
        self.assertEqual(p["would_delete_unkept"], len(older) if shortening else 0, p)
        self.assertEqual(self.days_in_settings(), START, "asking for the plan must not write anything")

        # 2. keep some: the plan now counts only the ones left unprotected
        self.keep(*kept)
        p = self.plan(days)
        self.assertEqual(p["would_delete"], len(older) if shortening else 0, p)
        self.assertEqual(p["would_delete_unkept"], len(older - kept) if shortening else 0, p)

        # 3. change it: only that key moves, and the old file is backed up
        code, out, err = self.cli("retention", str(days), "--yes")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.days_in_settings(), days)
        data = json.loads(self.settings_path.read_text())
        self.assertEqual((data["model"], data["env"]), ("opus", {"A": "1"}))
        _, shown, _ = self.cli("retention")
        self.assertIn("%d days" % days, shown)

        # 4. the menu bar's numbers agree with the same arithmetic done independently
        soon = {a for a in AGES if days - a < 14}  # "expires within 14 days" is what the menu promises
        r = menu.build(self.index())["retention"]
        self.assertEqual((r["days"], r["is_default"], r["expiring_within"]), (days, False, 14))
        self.assertEqual(r["expiring"], len(soon), r)
        self.assertEqual(r["expiring_unkept"], len(soon - kept), r)

        # 5. Claude's cleanup runs: exactly the sessions older than the setting are gone
        before = self.on_disk()
        deleted = self.claude_cleanup(days)
        self.assertEqual(deleted, older)
        self.assertEqual(self.on_disk(), before - older)

        # 6. a kept session whose original was deleted comes back, byte for byte, and Claude's cleanup then leaves it
        for age in sorted(older & kept):
            s = self.session(age)
            self.assertFalse(s["transcript"]["present"], age)
            plan = resume.plan(s, rt=self.make_rt())
            self.assertTrue(plan["restore"], "age %s should be restorable" % age)
            code, out, err = self.cli("restore", "claude:" + sid_for(age))
            self.assertEqual(code, 0, out + err)
            self.assertEqual(self.paths[age].read_bytes(), self.original[age], age)
            self.assert_just_written(self.paths[age])
        self.assertEqual(self.claude_cleanup(days), set())

        # 7. one that was never kept or saved leaves no trace (there is nothing to list), and asking for it fails cleanly
        listed = self.index().sessions
        for age in sorted(older - kept):
            self.assertNotIn("claude:" + sid_for(age), listed, age)
            code, out, err = self.cli("restore", "claude:" + sid_for(age))
            self.assertEqual(code, 1, age)
            self.assertFalse(self.paths[age].exists())
        for age in sorted(set(AGES) - older):
            self.assertIn("claude:" + sid_for(age), listed, age)  # everything inside the setting is still listed

        # 8. time passes: after more than `days` days Claude sweeps the restored files again; the copies still bring them back
        later = NOW + (days + 1) * DAY
        again = self.claude_cleanup(days, now=later)
        self.assertTrue(again >= (older & kept))
        for age in sorted(older & kept):
            code, out, err = self.cli("restore", "claude:" + sid_for(age), now=later)
            self.assertEqual(code, 0, out + err)
            self.assertEqual(self.paths[age].read_bytes(), self.original[age], age)
            self.assert_just_written(self.paths[age])

    story.__doc__ = "retention %d days" % days
    return story


for _days in (1, 7, 30, 45, 90, 365, 700, 3650):
    setattr(Seeded, "test_a_retention_of_%d_days_from_warning_to_restore" % _days, make_case(_days))


class Particulars(Seeded):
    def test_the_oldest_and_newest_ends_of_the_range_are_accepted_and_the_rest_refused(self):
        for ok in (1, 3650):
            code, out, err = self.cli("retention", str(ok), "--yes")
            self.assertEqual(code, 0, out + err)
            self.assertEqual(self.days_in_settings(), ok)
        before = self.settings_path.read_text()
        for bad in ("0", "3651", "-1", "100000"):
            code, _out, err = self.cli("retention", bad, "--yes")
            self.assertNotEqual(code, 0, bad)
            self.assertEqual(self.settings_path.read_text(), before, bad)

    def test_a_session_exactly_on_the_line_is_warned_about_but_claude_keeps_it_until_it_is_older(self):
        exact = self.claude_session("eeee5555-0000-4000-8000-000000000005", NOW - 50 * DAY - 600, NOW - 50 * DAY, title="Exactly 50 days")
        self.paths[50.0] = exact
        self.assertEqual(self.plan(50)["would_delete"], len([a for a in AGES if a > 50]) + 1)  # chainlog warns early: the safe side
        self.assertNotIn(50.0, self.claude_cleanup(50))  # Claude's rule is a strict "older than"
        self.assertTrue(exact.exists())
        self.assertIn(50.0, self.claude_cleanup(50, now=NOW + 60))  # a minute later it is older than 50 days
        self.assertFalse(exact.exists())

    def test_lengthening_in_time_saves_what_the_old_setting_was_about_to_take(self):
        self.set_settings({"cleanupPeriodDays": 30})  # Claude has not run for a while: ages 30.5 .. 89.5 are overdue
        r = menu.build(self.index())["retention"]
        overdue = {a for a in AGES if 30 - a < 14}
        self.assertEqual(r["expiring"], len(overdue))
        self.assertEqual(self.plan(90)["would_delete"], 0)  # longer: nothing is lost
        code, out, err = self.cli("retention", "90", "--yes")
        self.assertEqual(code, 0, out + err)
        deleted = self.claude_cleanup(90)
        self.assertEqual(deleted, {a for a in AGES if a > 90})
        for saved in (30.5, 44.5, 59.5, 60.5, 89.5):  # all past 30 days, all inside 90: saved by the change
            self.assertTrue(self.paths[saved].exists(), saved)

    def test_the_default_puts_claudes_own_thirty_days_back(self):
        code, out, err = self.cli("retention", "--default", "--yes")
        self.assertEqual(code, 0, out + err)
        data = json.loads(self.settings_path.read_text())
        self.assertNotIn("cleanupPeriodDays", data)
        self.assertEqual(data["model"], "opus")
        r = menu.build(self.index())["retention"]
        self.assertEqual((r["days"], r["is_default"]), (30, True))
        self.assertEqual(self.claude_cleanup(30), {a for a in AGES if a > 30})

    def test_what_you_saved_or_starred_survives_a_shortened_setting_without_being_asked_to(self):
        # keep.auto is on by default: saving or starring copies the transcript while Claude still has it
        self.cli("star", "claude:" + sid_for(180.5))
        self.cli("save", "--id", "claude:" + sid_for(364.5), "--", "worth remembering")
        self.assertTrue(self.session(180.5)["kept"] and self.session(364.5)["kept"])
        p = self.plan(30)
        self.assertEqual(p["would_delete"] - p["would_delete_unkept"], 2)
        self.cli("retention", "30", "--yes")
        self.claude_cleanup(30)
        for age in (180.5, 364.5):
            self.assertFalse(self.paths[age].exists())
            code, out, err = self.cli("restore", "claude:" + sid_for(age))
            self.assertEqual(code, 0, out + err)
            self.assertEqual(self.paths[age].read_bytes(), self.original[age])
        self.assertFalse(self.paths[800.5].exists())  # the other old ones were not kept and are gone

    def test_opening_a_deleted_kept_session_restores_it_first(self):
        self.keep(90.5)
        self.cli("retention", "30", "--yes")
        self.claude_cleanup(30)
        self.assertFalse(self.paths[90.5].exists())
        s = self.session(90.5)
        plan = resume.plan(s, rt=self.make_rt())
        self.assertTrue(plan["restore"])
        command = resume.shell_command(plan)  # the command it prints brings the file back before resuming
        self.assertTrue(command.startswith("chainlog restore claude:%s && " % s["native_id"]), command)

    def test_a_kept_session_that_kept_growing_is_copied_again_before_it_can_expire(self):
        self.keep(12.5)
        p = self.paths[12.5]
        grown = self.original[12.5] + b'{"type":"assistant","sessionId":"x","message":{"role":"assistant","content":"later work"}}\n'
        p.write_bytes(grown)
        os.utime(p, (NOW - 12 * DAY, NOW - 12 * DAY))  # older than the quiet period, so the refresh may copy it
        code, out, err = self.cli("keep", "--refresh")
        self.assertEqual(code, 0, out + err)
        self.cli("retention", "7", "--yes")
        self.claude_cleanup(7)
        self.assertFalse(p.exists())
        self.cli("restore", "claude:" + sid_for(12.5))
        self.assertEqual(p.read_bytes(), grown)  # the newest work came back, not the first copy

    def test_the_menu_row_says_a_session_is_kept_and_how_long_the_others_have(self):
        self.keep(29.5)
        self.cli("retention", "30", "--yes")
        rows = {r["short"]: r for r in menu.build(self.index(), unsaved_limit=50, recent_limit=50)["recent"]
                + menu.build(self.index(), unsaved_limit=50, recent_limit=50)["unsaved"]}
        self.assertTrue(rows[sid_for(29.5)[:8]]["kept"])
        self.assertFalse(rows[sid_for(20.5)[:8]]["kept"])
        left = keep.days_left(self.make_rt(), self.session(20.5))
        self.assertAlmostEqual(left, 30 - 20.5, places=3)


if __name__ == "__main__":
    unittest.main()
