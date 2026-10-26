"""The Mac menu bar app: the JSON it reads (`chainlog _menu`) and `chainlog setup menubar`."""
import io
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from helpers import NOW, ROOT, Home

from chainlog import cli
from chainlog import config as config_mod
from chainlog import menu as menu_mod
from chainlog import setup as setup_mod
from chainlog.index import load
from chainlog.integrations import components as comp

H = 3600
ID = ["%08d-0000-0000-0000-000000000000" % i for i in range(1, 12)]


class Args:
    def __init__(self, **kw):
        self.__dict__.update(dict(purge=False, yes=True, components=[], dry_run=False, skip=[], policy=None,
                                  engine=None, local=False, json=False))
        self.__dict__.update(kw)


class MenuData(Home):
    def setUp(self):
        super().setUp()
        rt = self.make_rt()
        cfg = config_mod.load(rt)
        cfg["installed_at"] = NOW - 200 * H
        config_mod.save(rt, cfg)
        self.rt = rt

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, rt=self.rt)
        return code, out.getvalue(), err.getvalue()

    def build(self, **kw):
        return menu_mod.build(load(self.rt, config_mod.load(self.rt)), **kw)


class MenuJson(MenuData):
    def test_unsaved_recent_and_counts(self):
        for i in range(5):  # newest first: ID[4] ... ID[0]
            self.claude_session(ID[i], NOW - (10 - i) * H, NOW - (9 - i) * H, title="Shop PR %d review" % (4400 + i))
        data = self.build(unsaved_limit=3, recent_limit=2)
        self.assertEqual(data["schema"], "chainlog.menu/1")
        self.assertEqual(data["counts"]["unsaved"], 5)  # the total, not just what is listed
        self.assertEqual([r["title"] for r in data["unsaved"]],
                         ["Shop PR 4404 review", "Shop PR 4403 review", "Shop PR 4402 review"])
        self.assertEqual([r["title"] for r in data["recent"]], ["Shop PR 4401 review", "Shop PR 4400 review"])
        shown = {r["ref"] for r in data["unsaved"]}
        self.assertFalse(shown & {r["ref"] for r in data["recent"]})  # nothing is listed twice

    def test_a_row_carries_the_ready_made_resume_command(self):
        self.claude_session(ID[0], NOW - 3 * H, NOW - 2 * H, title="Shop PR 4402 emails")
        row = self.build()["unsaved"][0]
        self.assertEqual(row["saved"], "none")
        self.assertEqual(row["ago"], "2h")
        self.assertEqual(row["resume"]["blockers"], [])
        self.assertEqual(row["resume"]["command"], "cd %s && claude --resume %s" % (self.proj, ID[0]))

    def test_saved_sessions_leave_unsaved_and_show_their_chain_round_and_status(self):
        self.claude_session(ID[0], NOW - 30 * H, NOW - 29 * H, title="Shop PR 4321 refund", pr=4321)
        self.claude_session(ID[1], NOW - 5 * H, NOW - 4 * H, title="Shop PR 4321 refund again", pr=4321)
        self.run_cli(["save", "--id", ID[0][:8], "r1", "-s", "pass"])
        self.run_cli(["save", "--id", ID[1][:8], "r2", "-s", "fail"])
        data = self.build()
        self.assertEqual(data["counts"]["unsaved"], 0)
        self.assertEqual(data["unsaved"], [])
        newest = data["recent"][0]
        self.assertEqual((newest["chain"], newest["round"], newest["status"], newest["saved"]),
                         ("shop#4321", 2, "fail", "user"))
        (chain,) = data["chains"]
        self.assertEqual((chain["id"], chain["rounds"], chain["status"]), ("shop#4321", 2, "fail"))
        self.assertEqual([m["round"] for m in chain["members"]], [1, 2])

    def test_a_deleted_transcript_is_listed_but_cannot_be_reopened(self):
        path = self.claude_session(ID[0], NOW - 30 * H, NOW - 29 * H, title="Shop PR 4321 refund", pr=4321)
        self.run_cli(["save", "--id", ID[0][:8], "r1"])
        os.remove(path)
        row = self.build()["recent"][0]
        self.assertEqual(row["title"], "Shop PR 4321 refund")
        self.assertTrue(any("transcript" in b for b in row["resume"]["blockers"]))

    def test_titles_are_one_clean_line(self):
        self.claude_session(ID[0], NOW - 3 * H, NOW - 2 * H, title="Shop PR 1\x1b[31m red\n  second line")
        title = self.build()["unsaved"][0]["title"]
        self.assertNotRegex(title, r"[\x00-\x1f\x7f]")
        self.assertIn("second line", title)

    def test_codex_sessions_resume_with_codex(self):
        self.codex_session("01a2b3c4-0000-0000-0000-000000000003", NOW - 3 * H, NOW - 2 * H, name="Shop PR 9 retest")
        row = self.build()["unsaved"][0]
        self.assertEqual(row["harness"], "codex")
        self.assertIn("codex resume", row["resume"]["command"])

    def test_nothing_at_all_is_still_valid(self):
        data = self.build()
        self.assertEqual((data["counts"]["unsaved"], data["unsaved"], data["recent"], data["chains"]),
                         (0, [], [], []))


class MenuCommand(MenuData):
    def test_prints_one_line_of_json_and_stays_out_of_help(self):
        self.claude_session(ID[0], NOW - 3 * H, NOW - 2 * H, title="Shop PR 4402 emails")
        code, out, _err = self.run_cli(["_menu"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertEqual(json.loads(out)["schema"], "chainlog.menu/1")
        self.assertNotIn("_menu", cli.build_parser().format_help())

    def test_the_sample_the_app_is_checked_against_has_the_same_shape(self):
        self.claude_session(ID[0], NOW - 30 * H, NOW - 29 * H, title="Shop PR 4321 refund", pr=4321)
        self.run_cli(["save", "--id", ID[0][:8], "r1", "-s", "pass"])
        live = self.build()
        sample = json.loads((ROOT / "plugin" / "menubar" / "sample.json").read_text())
        self.assertEqual(set(sample), set(live))
        self.assertEqual(set(sample["recent"][0]), set(live["recent"][0]))
        self.assertEqual(set(sample["recent"][0]["resume"]), set(live["recent"][0]["resume"]))
        self.assertEqual(set(sample["chains"][0]), set(live["chains"][0]))
        self.assertTrue(sample["schema"].startswith("chainlog.menu/1"))

    def test_the_sample_holds_no_real_paths(self):
        text = (ROOT / "plugin" / "menubar" / "sample.json").read_text()
        self.assertNotIn(os.path.expanduser("~"), text)


@unittest.skipUnless(sys.platform == "darwin", "the menu bar app is macOS only")
class BuildScript(Home):
    """plugin/menubar/build.sh with stand-ins for swiftc, open, pkill and codesign: installing, replacing
    and removing without compiling anything or touching a real app. (The stand-ins are made once: macOS
    vets every freshly written script the first time it runs, which would cost seconds per test.)"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory(prefix="chainlog-shims-")
        cls.base = Path(cls._tmp.name)
        cls.write_shim(cls.base, "swiftc", 'out=; while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2 && shift; shift; done\n'
                                           '[ -n "$out" ] || exit 2\nprintf "#!/bin/sh\\n" > "$out"; chmod +x "$out"\n')
        for name in ("open", "pkill", "codesign"):
            cls.write_shim(cls.base, name, 'echo "%s $*" >> "$CALLS"\n' % name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def write_shim(where, name, body):
        p = Path(where) / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)

    def setUp(self):
        super().setUp()
        self.extra = self.root / "shims"  # per-test stand-ins, found before the shared ones
        self.extra.mkdir()
        self.calls = self.root / "calls.log"
        self.script = ROOT / "plugin" / "menubar" / "build.sh"

    def shim(self, name, body):
        self.write_shim(self.extra, name, body)

    def run_build(self, apps, *args):
        env = dict(os.environ, PATH="%s:%s:%s" % (self.extra, self.base, os.environ["PATH"]),
                   CHAINLOG_APP_DIR=str(apps), CALLS=str(self.calls))
        return subprocess.run(["sh", str(self.script), *args], env=env, capture_output=True, text=True, timeout=60)

    def log(self):
        return self.calls.read_text() if self.calls.exists() else ""

    def test_installs_into_a_path_with_a_space_and_an_apostrophe(self):
        apps = self.root / "my apps" / "it's here"
        p = self.run_build(apps, "--install")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((apps / "Chainlog.app" / "Contents" / "MacOS" / "Chainlog").exists())
        self.assertIn("<string>%s</string>" % (ROOT / "plugin" / "VERSION").read_text().strip(),
                      (apps / "Chainlog.app" / "Contents" / "Info.plist").read_text())
        self.assertIn("open %s" % (apps / "Chainlog.app"), self.log())

    def test_replacing_leaves_no_staging_folder_behind(self):
        apps = self.root / "apps"
        self.run_build(apps, "--install")
        self.assertEqual(self.run_build(apps, "--install").returncode, 0)
        self.assertEqual(os.listdir(apps), ["Chainlog.app"])

    def test_a_failed_build_leaves_the_installed_app_and_the_running_one_alone(self):
        apps = self.root / "apps"
        self.run_build(apps, "--install")
        exe = apps / "Chainlog.app" / "Contents" / "MacOS" / "Chainlog"
        exe.write_text("the app that works")
        self.calls.unlink()
        self.shim("swiftc", "echo 'error: simulated' >&2\nexit 1\n")
        p = self.run_build(apps, "--install")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("build failed", p.stderr)
        self.assertEqual(exe.read_text(), "the app that works")
        self.assertNotIn("pkill", self.log(), "the running app must not be quit when the build failed")

    def test_a_folder_that_cannot_be_written_keeps_the_old_app(self):
        apps = self.root / "ro"
        exe = apps / "Chainlog.app" / "Contents" / "MacOS" / "Chainlog"
        exe.parent.mkdir(parents=True)
        exe.write_text("old")
        os.chmod(apps, 0o500)
        try:
            p = self.run_build(apps, "--install")
        finally:
            os.chmod(apps, 0o700)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("cannot write", p.stderr)
        self.assertEqual(exe.read_text(), "old")
        self.assertNotIn("pkill", self.log())

    def test_uninstall_quits_and_removes_and_is_harmless_twice(self):
        apps = self.root / "apps"
        self.run_build(apps, "--install")
        for _ in range(2):
            self.assertEqual(self.run_build(apps, "--uninstall").returncode, 0)
        self.assertFalse((apps / "Chainlog.app").exists())
        self.assertIn("pkill -x Chainlog", self.log())

    def test_an_unknown_option_is_refused(self):
        p = self.run_build(self.root / "apps", "--frobnicate")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("usage", p.stderr)

    def test_only_macos_builds(self):
        self.shim("uname", "echo Linux\n")
        p = self.run_build(self.root / "apps", "--install")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("macOS", p.stderr)


class SetupMenubar(Home):
    def setUp(self):
        super().setUp()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        swiftc = self.bin / "swiftc"
        swiftc.write_text("#!/bin/sh\n")
        swiftc.chmod(0o755)
        self.apps = self.root / "Applications"

    def rt_(self, swiftc=True):
        env = {"PATH": str(self.bin) if swiftc else str(self.root / "empty"), "CHAINLOG_APP_DIR": str(self.apps)}
        return self.make_rt(env=env)

    def darwin(self):
        return mock.patch.object(comp.sys, "platform", "darwin")

    def test_plans_one_build_and_a_matching_undo(self):
        with self.darwin():
            (ch,) = comp.plan_menubar(self.rt_())
        self.assertEqual(ch.command[:2], ["env", "CHAINLOG_APP_DIR=%s" % self.apps])
        self.assertTrue(ch.command[-2].endswith("menubar/build.sh") and ch.command[-1] == "--install")
        self.assertIn(str(self.apps / "Chainlog.app"), ch.undo_command)
        self.assertIn("build and install", ch.description)

    def test_nothing_without_a_mac_or_swiftc(self):
        with mock.patch.object(comp.sys, "platform", "linux"):
            self.assertEqual(comp.plan_menubar(self.rt_()), [])
        with self.darwin():
            self.assertEqual(comp.plan_menubar(self.rt_(swiftc=False)), [])

    def test_nothing_when_this_version_is_installed_and_an_update_when_older(self):
        plist = self.apps / "Chainlog.app" / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True)
        for installed, expect in ((comp.version(), 0), ("0.0.1", 1)):
            plist.write_text('<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleShortVersionString</key>'
                             "<string>%s</string></dict></plist>" % installed)
            with self.darwin():
                changes = comp.plan_menubar(self.rt_())
            self.assertEqual(len(changes), expect)
        self.assertIn("update", changes[0].description)

    def test_all_leaves_the_menu_bar_app_out_but_naming_it_asks_for_it(self):
        out = []
        with self.darwin():
            setup_mod.run(self.rt_(), Args(dry_run=True, skip=["claude", "codex", "shell", "policy", "cli"]), out.append)
            self.assertNotIn("[menubar]", "\n".join(out))
            out.clear()
            setup_mod.run(self.rt_(), Args(dry_run=True, components=["menubar"]), out.append)
        self.assertIn("[menubar]", "\n".join(out))
        self.assertIn("build.sh --install", "\n".join(out))

    def test_asking_for_it_without_swiftc_says_what_is_missing(self):
        out = []
        with self.darwin():
            setup_mod.run(self.rt_(swiftc=False), Args(dry_run=True, components=["menubar"]), out.append)
        self.assertIn("Xcode command line tools", "\n".join(out))


if __name__ == "__main__":
    unittest.main()
