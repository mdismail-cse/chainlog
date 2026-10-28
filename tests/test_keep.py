"""Keeping sessions Claude would delete, restoring them on resume, deleting from either side, and the retention setting.
Everything runs in a fake home folder: no test touches the real ~/.claude."""
import gzip
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import NOW, Home

from chainlog import cli
from chainlog import config as config_mod
from chainlog import delete as delete_mod
from chainlog import keep, resume
from chainlog import retention as retention_mod
from chainlog.index import load

DAY = 86400
A = "aaaa1111-0000-4000-8000-000000000001"  # 20 days old
B = "bbbb2222-0000-4000-8000-000000000002"  # an hour old
C = "cccc3333-0000-4000-8000-000000000003"  # 3 days old


class KeepCase(Home):
    def setUp(self):
        super().setUp()
        self.pa = self.claude_session(A, NOW - 20 * DAY - 600, NOW - 20 * DAY, title="Old refund work", pr=4321)
        self.pb = self.claude_session(B, NOW - 4000, NOW - 3600, title="Recent export work")
        self.pc = self.claude_session(C, NOW - 3 * DAY - 600, NOW - 3 * DAY, title="Middle work", entrypoint="claude-vscode")

    def cli(self, argv, tty=False, answers=(), env=None):
        rt = self.make_rt(tty=tty, env=env)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", side_effect=list(answers)) as inp, redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, rt=rt)
        self.asked = [c.args[0] if c.args else "" for c in inp.call_args_list]
        return code, out.getvalue(), err.getvalue()

    def index(self):
        return load(self.make_rt(), config_mod.load(self.make_rt()))

    def session(self, sid):
        return self.index().sessions["claude:" + sid]

    def kept_files(self, sid):
        return keep.files(self.make_rt(), {"native_id": sid})


class Keeping(KeepCase):
    def test_a_copy_is_compressed_and_decompresses_to_exactly_the_original(self):
        code, out, _ = self.cli(["keep", "claude:" + A])
        self.assertEqual(code, 0, out)
        gz, details = self.kept_files(A)
        self.assertEqual(gzip.open(gz).read(), self.pa.read_bytes())
        m = json.loads(details.read_text())
        self.assertEqual((m["orig_path"], m["size"]), (str(self.pa), self.pa.stat().st_size))
        self.assertEqual(oct(gz.stat().st_mode & 0o777), "0o600")  # private, like the rest of chainlog's data
        self.assertTrue(self.session(A)["kept"])
        self.assertEqual(self.pa.read_bytes(), self.pa.read_bytes())  # the original is untouched

    def test_keeping_twice_records_one_event_and_ls_kept_lists_it(self):
        self.cli(["keep", "claude:" + A])
        self.cli(["keep", "claude:" + A])
        self.assertEqual(sum(1 for e in self.index().events if e["type"] == "keep"), 1)
        code, out, _ = self.cli(["ls", "--kept", "--json"])
        self.assertEqual([s["native_id"] for s in json.loads(out)], [A])

    def test_a_codex_session_is_refused_with_the_reason(self):
        self.codex_session("dddd4444-0000-4000-8000-000000000004", NOW - 900, NOW - 600)
        code, out, err = self.cli(["keep", "codex:dddd4444"])
        self.assertEqual(code, 1)
        self.assertIn("Codex does not delete", err)

    def test_nothing_to_copy_when_claude_already_deleted_it(self):
        self.cli(["save", "--id", "claude:" + B, "note", "--no-chain"])
        os.unlink(self.pb)
        code, _, err = self.cli(["keep", "claude:" + B])
        self.assertEqual(code, 1)
        self.assertIn("already deleted", err)

    def test_refresh_catches_up_with_a_session_that_kept_growing_but_not_one_being_written(self):
        self.cli(["keep", "claude:" + A, "claude:" + C])
        for p in (self.pa, self.pc):
            with open(p, "a") as f:
                f.write(json.dumps({"type": "last-prompt", "lastPrompt": "and then", "sessionId": "x"}) + "\n")
        os.utime(self.pa, (NOW - 3600, NOW - 3600))  # idle for an hour
        os.utime(self.pc, (NOW - 5, NOW - 5))  # written 5 seconds ago: still running
        code, out, _ = self.cli(["keep", "--refresh"])
        self.assertEqual(code, 0)
        self.assertEqual(gzip.open(self.kept_files(A)[0]).read(), self.pa.read_bytes())
        self.assertNotEqual(gzip.open(self.kept_files(C)[0]).read(), self.pc.read_bytes())  # picked up on a later refresh
        self.assertIn("refreshed 1", out)
        code, out, _ = self.cli(["keep", "--refresh"])
        self.assertIn("up to date", out)

    def test_unkeep_removes_the_copy_and_leaves_the_original(self):
        self.cli(["keep", "claude:" + A])
        code, out, _ = self.cli(["unkeep", "claude:" + A])
        self.assertEqual(code, 0)
        self.assertFalse(self.kept_files(A)[0].exists() or self.kept_files(A)[1].exists())
        self.assertTrue(self.pa.exists())
        self.assertFalse(self.session(A)["kept"])

    def test_dropping_the_only_copy_needs_a_yes(self):
        self.cli(["keep", "claude:" + A])
        os.unlink(self.pa)
        code, _, err = self.cli(["unkeep", "claude:" + A])  # not a terminal, no --yes
        self.assertEqual(code, 1)
        self.assertTrue(self.kept_files(A)[0].exists())
        code, _, _ = self.cli(["unkeep", "claude:" + A], tty=True, answers=["n"])
        self.assertTrue(self.kept_files(A)[0].exists())
        self.assertIn("all that is left", self.asked[0])
        code, _, _ = self.cli(["unkeep", "claude:" + A, "--yes"])
        self.assertFalse(self.kept_files(A)[0].exists())


class Automatically(KeepCase):
    def test_saving_a_session_keeps_it(self):
        code, out, _ = self.cli(["save", "--id", "claude:" + A, "refund fixed", "--no-chain"])
        self.assertEqual(code, 0)
        self.assertTrue(keep.has_copy(self.make_rt(), self.session(A)))
        self.assertIn("kept", out)
        self.assertIn("a copy", out)

    def test_starring_keeps_it_and_unstarring_does_not_delete_the_copy(self):
        self.cli(["star", "claude:" + A])
        self.assertTrue(keep.has_copy(self.make_rt(), self.session(A)))
        self.cli(["unstar", "claude:" + A])
        self.assertTrue(keep.has_copy(self.make_rt(), self.session(A)))  # only unkeep drops it

    def test_it_can_be_turned_off(self):
        self.cli(["config", "set", "keep.auto", "false"])
        self.cli(["save", "--id", "claude:" + A, "refund fixed", "--no-chain"])
        self.cli(["star", "claude:" + B])
        self.assertFalse(keep.has_copy(self.make_rt(), self.session(A)) or keep.has_copy(self.make_rt(), self.session(B)))

    def test_starring_a_session_whose_transcript_is_gone_still_works_and_keeps_nothing(self):
        self.cli(["config", "set", "keep.auto", "false"])
        self.cli(["save", "--id", "claude:" + B, "note", "--no-chain"])  # chainlog now remembers it
        self.cli(["config", "set", "keep.auto", "true"])
        os.unlink(self.pb)
        code, out, _ = self.cli(["star", "claude:" + B])
        self.assertEqual(code, 0, out)
        self.assertFalse(keep.has_copy(self.make_rt(), self.session(B)))

    def test_the_json_output_of_save_is_still_only_json(self):
        code, out, _ = self.cli(["save", "--id", "claude:" + A, "refund fixed", "--no-chain", "--json"])
        self.assertEqual(json.loads(out)["saved"]["native_id"], A)
        self.assertTrue(keep.has_copy(self.make_rt(), self.session(A)))


class Restoring(KeepCase):
    def kept_then_deleted(self):
        self.cli(["keep", "claude:" + A])
        original = self.pa.read_bytes()
        os.unlink(self.pa)
        return original

    def test_a_kept_session_can_be_resumed_after_claude_deleted_it(self):
        self.kept_then_deleted()
        p = resume.plan(self.session(A), rt=self.make_rt())
        self.assertEqual(p["blockers"], [])
        self.assertTrue(p["restore"])
        self.assertTrue(resume.shell_command(p).startswith("chainlog restore claude:%s && cd " % A), resume.shell_command(p))

    def test_resume_puts_the_copy_back_first(self):
        original = self.kept_then_deleted()
        opened = []
        with mock.patch("chainlog.resume.execute", side_effect=lambda p: opened.append(p)):
            code, _, err = self.cli(["resume", "claude:" + A])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.pa.read_bytes(), original)
        self.assertEqual(opened[0]["argv"], ["claude", "--resume", A])
        self.assertIn("restored your kept copy", err)

    def test_the_restored_file_is_new_so_claudes_cleanup_does_not_sweep_it_at_once(self):
        self.kept_then_deleted()
        with mock.patch("chainlog.resume.execute"):
            self.cli(["resume", "claude:" + A])
        import time
        self.assertLess(time.time() - self.pa.stat().st_mtime, 60)  # not the 20-day-old time of the original

    def test_restore_is_a_command_of_its_own_and_is_idempotent(self):
        original = self.kept_then_deleted()
        code, out, _ = self.cli(["restore", "claude:" + A])
        self.assertEqual((code, self.pa.read_bytes()), (0, original))
        self.assertIn("restored", out)
        code, out, _ = self.cli(["restore", "claude:" + A])
        self.assertIn("already where Claude keeps it", out)

    def test_without_a_copy_the_blocker_says_how_to_avoid_it_next_time(self):
        self.cli(["save", "--id", "claude:" + B, "note", "--no-chain", "--json"])  # saved (and so kept): drop the copy
        self.cli(["unkeep", "claude:" + B, "--yes"])
        os.unlink(self.pb)
        p = resume.plan(self.session(B), rt=self.make_rt())
        self.assertFalse(p["restore"])
        self.assertIn("chainlog keep", p["blockers"][0])

    def test_a_copy_that_points_outside_claudes_folders_is_not_restored(self):
        self.kept_then_deleted()
        _gz, details = self.kept_files(A)
        m = json.loads(details.read_text())
        m["orig_path"] = str(self.root / "elsewhere" / (A + ".jsonl"))
        details.write_text(json.dumps(m))
        code, _, err = self.cli(["restore", "claude:" + A])
        self.assertEqual(code, 1)
        self.assertIn("outside Claude's project folders", err)
        self.assertFalse((self.root / "elsewhere").exists())

    def test_a_damaged_copy_leaves_nothing_half_written(self):
        self.kept_then_deleted()
        gz, _ = self.kept_files(A)
        gz.write_bytes(gz.read_bytes()[:40])  # truncated
        code, _, err = self.cli(["restore", "claude:" + A])
        self.assertEqual(code, 1)
        self.assertFalse(self.pa.exists())
        self.assertEqual([p.name for p in self.pa.parent.iterdir() if A in p.name], [])

    def test_the_menu_row_says_it_restores_and_the_command_does_it(self):
        self.kept_then_deleted()
        from chainlog import menu
        d = menu.build(self.index())
        row = next(r for r in d["unsaved"] + d["recent"] if r["short"] == A[:8])
        self.assertTrue(row["kept"])
        self.assertTrue(row["resume"]["restores"])
        self.assertEqual(row["resume"]["blockers"], [])
        self.assertTrue(row["resume"]["command"].startswith("chainlog restore "))

    def test_reopening_in_vscode_restores_first_too(self):
        self.cli(["keep", "claude:" + C])
        os.unlink(self.pc)
        s = self.session(C)
        p = resume.plan(s, rt=self.make_rt())
        cmd = resume.vscode_command(s, p, platform="darwin")
        self.assertTrue(cmd.startswith("chainlog restore claude:%s && open -a 'Visual Studio Code'" % C), cmd)


class Deleting(KeepCase):
    def test_both_sides_remove_the_transcript_its_folder_the_copy_and_the_listing(self):
        self.cli(["keep", "claude:" + A])
        folder = self.pa.with_suffix("")
        (folder / "subagents").mkdir(parents=True)
        (folder / "subagents" / "agent-1.jsonl").write_text("{}\n")
        code, out, _ = self.cli(["delete", "claude:" + A, "--yes"])
        self.assertEqual(code, 0, out)
        self.assertFalse(self.pa.exists() or folder.exists())
        self.assertFalse(self.kept_files(A)[0].exists())
        self.assertNotIn("claude:" + A, {s["ref"] for s in self.index().visible()})
        self.assertTrue(self.pb.exists() and self.pc.exists())  # nothing else is touched

    def test_only_claude_keeps_chainlogs_copy_so_it_can_come_back(self):
        self.cli(["keep", "claude:" + A])
        code, _, _ = self.cli(["delete", "claude:" + A, "--from", "claude", "--yes"])
        self.assertEqual(code, 0)
        self.assertFalse(self.pa.exists())
        self.assertTrue(keep.has_copy(self.make_rt(), self.session(A)))
        self.assertTrue(resume.plan(self.session(A), rt=self.make_rt())["restore"])

    def test_only_chainlog_leaves_claudes_file_alone(self):
        self.cli(["keep", "claude:" + A])
        original = self.pa.read_bytes()
        code, _, _ = self.cli(["delete", "claude:" + A, "--from", "chainlog", "--yes"])
        self.assertEqual(code, 0)
        self.assertEqual(self.pa.read_bytes(), original)
        self.assertFalse(self.kept_files(A)[0].exists())
        self.assertNotIn("claude:" + A, {s["ref"] for s in self.index().visible()})

    def test_undo_brings_the_listing_back(self):
        self.cli(["delete", "claude:" + A, "--from", "chainlog", "--yes"])
        self.cli(["undo"])
        self.assertIn("claude:" + A, {s["ref"] for s in self.index().visible()})

    def test_it_asks_first_and_nothing_happens_on_no(self):
        code, out, _ = self.cli(["delete", "claude:" + A], tty=True, answers=["n"])
        self.assertEqual(code, 1)
        self.assertTrue(self.pa.exists())
        self.assertIn("cannot be undone", self.asked[0])
        code, out, _ = self.cli(["delete", "claude:" + A], tty=True, answers=["y"])
        self.assertEqual(code, 0)
        self.assertFalse(self.pa.exists())

    def test_without_a_terminal_it_needs_yes(self):
        code, _, err = self.cli(["delete", "claude:" + A])
        self.assertEqual(code, 1)
        self.assertTrue(self.pa.exists())

    def test_a_running_session_is_refused_and_nothing_is_deleted(self):
        s = dict(self.session(A), live=True)
        p = delete_mod.plan(self.make_rt(), s)
        self.assertIn("still running", p["refused"])

    def test_one_refused_session_stops_the_whole_command(self):
        with mock.patch("chainlog.delete.plan", side_effect=lambda rt, s, where="both": {
                "claude": [], "kept": 0, "chainlog": True, "refused": "it is still running" if s["native_id"] == B else None}):
            code, _, err = self.cli(["delete", "claude:" + A, "claude:" + B, "--yes"])
        self.assertEqual(code, 1)
        self.assertTrue(self.pa.exists() and self.pb.exists())

    def test_it_only_ever_removes_a_transcript_named_after_the_session_inside_claudes_projects(self):
        rt = self.make_rt()
        s = self.session(A)
        stray = self.root / "work" / (A + ".jsonl")  # right name, real file, but not inside ~/.claude/projects/<project>/
        stray.write_text("not a Claude transcript\n")
        (self.pa.parent / "notes.jsonl").write_text("{}\n")  # in the right folder, but not named after the session
        for bad in (str(stray), str(self.pa.parent / "notes.jsonl"), str(self.home / ".claude" / "settings.json")):
            fake = dict(s, transcript={"path": bad, "present": True})
            self.assertEqual(delete_mod.claude_targets(rt, fake), [], bad)
            delete_mod.carry_out(rt, fake, "claude")
            self.assertTrue(os.path.exists(bad) or bad.endswith("settings.json"), bad)  # still there after a real attempt
        self.assertEqual(stray.read_text(), "not a Claude transcript\n")
        outside = self.root / "precious.jsonl"
        outside.write_text("keep me\n")
        link = self.pa.parent / ("dddd4444-0000-4000-8000-000000000004.jsonl")
        os.symlink(outside, link)
        self.assertEqual(delete_mod.claude_targets(rt, dict(s, native_id="dddd4444-0000-4000-8000-000000000004",
                                                             transcript={"path": str(link), "present": True})), [])
        self.assertEqual(outside.read_text(), "keep me\n")

    def test_a_symlinked_session_folder_is_left_alone(self):
        target = self.root / "somewhere"
        target.mkdir()
        (target / "file.txt").write_text("x")
        os.symlink(target, self.pa.with_suffix(""))
        self.cli(["delete", "claude:" + A, "--yes"])
        self.assertTrue((target / "file.txt").exists())

    def test_codex_sessions_can_only_be_deleted_from_chainlog(self):
        self.codex_session("dddd4444-0000-4000-8000-000000000004", NOW - 900, NOW - 600)
        code, _, err = self.cli(["delete", "codex:dddd4444", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("--from chainlog", err)
        code, _, _ = self.cli(["delete", "codex:dddd4444", "--from", "chainlog", "--yes"])
        self.assertEqual(code, 0)


class Retention(KeepCase):
    def settings(self, text=None):
        p = self.home / ".claude" / "settings.json"
        if text is not None:
            p.write_text(text)
        return p

    def test_it_says_how_long_claude_keeps_things_and_what_expires_soon(self):
        self.settings('{"cleanupPeriodDays": 21}\n')
        code, out, _ = self.cli(["retention"])
        self.assertEqual(code, 0)
        self.assertIn("21 days", out)
        self.assertIn("1 expire within 14 days", out)  # A: 20 days old of 21
        self.assertIn("1 without a kept copy", out)
        self.cli(["keep", "claude:" + A])
        _, out, _ = self.cli(["retention"])
        self.assertIn("0 without a kept copy", out)

    def test_with_nothing_set_it_reports_claudes_own_default(self):
        _, out, _ = self.cli(["retention"])
        self.assertIn("30 days", out)
        self.assertIn("is not set", out)

    def test_setting_it_changes_only_that_key_and_backs_the_old_file_up(self):
        original = '{\n  "model": "opus",\n  "env": {"A": "1"},\n  "cleanupPeriodDays": 30\n}\n'
        path = self.settings(original)
        code, out, _ = self.cli(["retention", "365", "--yes"])
        self.assertEqual(code, 0, out)
        self.assertEqual(path.read_text(), original.replace("30", "365"))
        backups = list((self.data / "backups").glob("*settings.json.bak"))
        self.assertEqual([b.read_text() for b in backups], [original])
        self.assertIn("365 days", out)

    def test_adding_the_key_to_a_file_that_lacks_it(self):
        path = self.settings('{\n  "model": "opus"\n}\n')
        self.cli(["retention", "120", "--yes"])
        self.assertEqual(json.loads(path.read_text()), {"model": "opus", "cleanupPeriodDays": 120})
        self.assertTrue(path.read_text().startswith('{\n  "cleanupPeriodDays": 120,\n  "model"'))

    def test_a_missing_or_empty_settings_file_is_created(self):
        path = self.settings()
        self.assertFalse(path.exists())
        self.cli(["retention", "90", "--yes"])
        self.assertEqual(json.loads(path.read_text()), {"cleanupPeriodDays": 90})

    def test_default_removes_the_key(self):
        path = self.settings('{\n  "model": "opus",\n  "cleanupPeriodDays": 90\n}\n')
        self.cli(["retention", "--default", "--yes"])
        self.assertEqual(json.loads(path.read_text()), {"model": "opus"})

    def test_shortening_it_warns_how_many_sessions_Claude_would_delete(self):
        self.settings('{"cleanupPeriodDays": 90}\n')
        code, out, _ = self.cli(["retention", "10", "--yes"])
        self.assertIn("1 of your sessions are older than 10 days", out)
        self.assertIn("1 have no kept copy", out)

    def test_it_asks_first_and_changes_nothing_on_no(self):
        path = self.settings('{"cleanupPeriodDays": 30}\n')
        code, out, _ = self.cli(["retention", "90"], tty=True, answers=["n"])
        self.assertEqual(code, 1)
        self.assertEqual(path.read_text(), '{"cleanupPeriodDays": 30}\n')
        code, _, _ = self.cli(["retention", "90"])  # no terminal, no --yes
        self.assertEqual(code, 1)
        self.assertEqual(path.read_text(), '{"cleanupPeriodDays": 30}\n')

    def test_a_broken_settings_file_is_never_touched(self):
        path = self.settings('{"model": ')
        code, _, err = self.cli(["retention", "90", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)
        self.assertEqual(path.read_text(), '{"model": ')

    def test_days_must_be_sensible(self):
        for bad in ("0", "-5", "abc", "999999"):
            try:
                code, _, _ = self.cli(["retention", bad, "--yes"])
            except SystemExit as e:  # argparse refuses a non-number outright
                code = e.code
            self.assertNotEqual(code, 0, bad)
        self.assertFalse((self.home / ".claude" / "settings.json").exists())

    def test_json_says_what_a_change_would_do_and_writes_nothing(self):
        path = self.settings('{"cleanupPeriodDays": 90}\n')
        code, out, _ = self.cli(["retention", "10", "--json"])  # A is 20 days old: it would go; it has no copy
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"current": 90, "to": 10, "changed": True, "would_delete": 1, "would_delete_unkept": 1})
        self.cli(["keep", "claude:" + A])
        self.assertEqual(json.loads(self.cli(["retention", "10", "--json"])[1])["would_delete_unkept"], 0)
        self.assertEqual(json.loads(self.cli(["retention", "365", "--json"])[1])["would_delete"], 0)  # longer: nothing is lost
        self.assertEqual(path.read_text(), '{"cleanupPeriodDays": 90}\n')
        self.assertFalse((self.data / "backups").exists())

    def test_the_menu_carries_what_the_gear_menu_shows(self):
        from chainlog import menu
        self.settings('{"cleanupPeriodDays": 21}\n')
        d = menu.build(self.index())
        self.assertEqual(d["retention"], {"days": 21, "is_default": False, "expiring": 1, "expiring_unkept": 1, "expiring_within": 14})
        self.assertTrue(d["keep_auto"])
        self.cli(["keep", "claude:" + A])
        self.assertEqual(menu.build(self.index())["retention"]["expiring_unkept"], 0)
        self.cli(["config", "set", "keep.auto", "false"])
        self.assertFalse(menu.build(self.index())["keep_auto"])
        self.settings('{}')
        self.assertEqual((lambda r: (r["days"], r["is_default"]))(menu.build(self.index())["retention"]), (30, True))

    def test_the_same_value_is_not_rewritten(self):
        self.settings('{"cleanupPeriodDays": 90}\n')
        code, out, _ = self.cli(["retention", "90", "--yes"])
        self.assertIn("already 90 days", out)
        self.assertFalse((self.data / "backups").exists() and list((self.data / "backups").glob("*settings*")))


class EditingSettings(unittest.TestCase):
    """The text edit changes only the one key, whatever the file looks like."""

    def check(self, text, days, want):
        new = retention_mod.edit(text, days)
        expect = json.loads(text) if text.strip() else {}
        if days is None:
            expect.pop("cleanupPeriodDays", None)
        else:
            expect["cleanupPeriodDays"] = days
        self.assertEqual(json.loads(new), expect)
        if want is not None:
            self.assertEqual(new, want)
        return new

    def test_replace_insert_and_remove_in_the_common_layouts(self):
        self.check('{"cleanupPeriodDays": 30}', 90, '{"cleanupPeriodDays": 90}')
        self.check('{"a": 1}', 90, '{\n  "cleanupPeriodDays": 90,"a": 1}')
        self.check('{\n    "a": 1\n}\n', 90, '{\n    "cleanupPeriodDays": 90,\n    "a": 1\n}\n')
        self.check('{\n  "a": 1,\n  "cleanupPeriodDays": 30\n}\n', None, None)  # last key: comma fix-up needed
        self.check('{\n  "cleanupPeriodDays": 30,\n  "a": 1\n}\n', None, '{\n  "a": 1\n}\n')
        self.check('{}', 45, None)
        self.check('', 45, None)
        self.check('{\r\n  "a": 1\r\n}\r\n', 45, None)

    def test_a_same_named_key_nested_elsewhere_is_not_the_one_edited(self):
        new = self.check('{"env": {"cleanupPeriodDays": "x"}, "cleanupPeriodDays": 30}', 90, None)
        self.assertEqual(json.loads(new)["env"], {"cleanupPeriodDays": "x"})

    def test_a_value_that_is_not_a_number_is_replaced(self):
        self.check('{"cleanupPeriodDays": "thirty"}', 90, None)

    def test_not_an_object_is_refused(self):
        with self.assertRaises(ValueError):
            retention_mod.edit("[1, 2]", 90)

    def test_current_reads_only_a_positive_number(self):
        for text, want in (('{"cleanupPeriodDays": 90}', 90), ('{"cleanupPeriodDays": 0}', None), ('{"cleanupPeriodDays": "9"}', None),
                           ('{"cleanupPeriodDays": true}', None), ('{}', None), ('nonsense', None)):
            self.assertEqual(retention_mod.current(text), want, text)


class Showing(KeepCase):
    def test_show_mentions_the_kept_copy_and_the_expiry_when_there_is_none(self):
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.write_text('{"cleanupPeriodDays": 21}\n')
        _, out, _ = self.cli(["show", "claude:" + A])
        self.assertIn("chainlog keep", out)  # 20 of 21 days used and not kept
        self.cli(["keep", "claude:" + A])
        _, out, _ = self.cli(["show", "claude:" + A])
        self.assertIn("a copy", out)
        self.assertNotIn("Claude deletes this transcript", out)


if __name__ == "__main__":
    unittest.main()
