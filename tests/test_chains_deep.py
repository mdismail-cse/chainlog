"""The Chains tab, deeply: a seeded world of chains of every shape, checked in the JSON the app reads (`chainlog _menu`),
in the command line (`chain`, `next`) and, on macOS, in what the app draws.

The big world is built once (it takes a few seconds: every `save` is the real command) and the tests only read it.
Tests that change things build their own small worlds. Everything runs in a fake home folder.

The seeded chains (start order is round order; hours are "hours ago", newest chain first):
  tracker#321            2 rounds  r1 blocked, r2 none            -c 321                        (n43 140h, n44 130h)
  release 3.2 regression 4 rounds  fail, pass, none, fail         free text, four spellings     (n10 400h, n11 380h, n42 160h, n45 150h)
  shop#4388              2 rounds  r1 PR link, r2 also in release  (n6 480h, n42 160h)
  api#4321               1 round   `-c 4321` from the api repo: not shop#4321                    (n41 180h)
  shop#5000              3 rounds  pinned order C, A, B            (n30 250h, n31 240h, n32 230h; pos 1, 2, 0)
  shop#9000              10 rounds only the last 8 are listed     (n20..n29, 360h .. 270h)
  api#177                3 rounds  r2's transcript deleted but kept, r3's deleted and not kept   (n7 460h, n8 440h, n9 420h)
  shop#4321              5 rounds  PR link, Codex title, VS Code branch, `-c 4321`, `-c <PR URL>` (n1..n5, 700h .. 500h)
Not in any chain: n40 (its PR link says 4321, its title says 4400: no guess)."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from helpers import ROOT, Home

from chainlog import cli
from chainlog import config as config_mod
from chainlog import menu, resume
from chainlog.index import load, write_events
from chainlog.store.events import new_event

H = 3600
T0 = float(int(time.time()))  # whole seconds; every command in the world runs at this moment
LAUNCHER = str(ROOT / "plugin" / "bin" / "chainlog")
CHAINS = ["tracker#321", "release 3.2 regression", "shop#4388", "api#4321", "shop#5000", "shop#9000", "api#177", "shop#4321"]


def sid(n):
    return "%08x-c4a1-4000-8000-%012x" % (n, n)


def csid(n):
    return "019f%04x-0000-7000-8000-%012x" % (n, n)  # a Codex thread id


class World(Home):
    """A fake home with the seeded chains. Built by hand (not run as a test)."""

    def runTest(self):  # lets unittest build one without a test name
        pass

    def t(self, hours_ago):
        return T0 - hours_ago * H

    def repo(self, name, remote):
        d = self.root / "work" / name
        (d / ".git").mkdir(parents=True)
        (d / ".git" / "config").write_text('[remote "origin"]\n\turl = %s\n' % remote)
        return d

    def cli(self, *argv, cwd=None, now=T0):
        rt = self.make_rt(now=now, cwd=cwd)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", side_effect=[]), redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(argv), rt=rt)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def session(self, n, start_h, cwd=None, title=None, **kw):
        """A Claude session that began `start_h` hours ago and lasted an hour."""
        return self.claude_session(sid(n), self.t(start_h), self.t(start_h - 1), title=title or "Work %d" % n,
                                   cwd=cwd or self.proj, **kw)

    def save(self, ref, note, *flags):
        code, out, err = self.cli("save", "--id", ref, *flags, "--", note)
        assert code == 0, (ref, flags, out, err)
        return out

    def link(self, ref, chain, pos=None, source="user"):
        rt = self.make_rt(now=T0)
        write_events(rt, [new_event(rt, "chain.link", ref, "cli", {"chain": chain, "pos": pos, "source": source})])

    def index(self):
        rt = self.make_rt(now=T0)
        return load(rt, config_mod.load(rt))

    def menu(self, **kw):
        return menu.build(self.index(), **kw)

    def seed(self):
        rt = self.make_rt(now=T0)
        cfg = config_mod.load(rt)
        cfg["installed_at"] = T0 - 2000 * H
        cfg["unsaved"]["lookback"] = 10 ** 8
        config_mod.save(rt, cfg)
        shop = self.proj
        self.api = self.repo("api", "git@github.com:acme/api.git")
        self.tracker = self.repo("tracker", "git@github.com:acme/tracker.git")
        c = lambda n: "claude:" + sid(n)  # noqa: E731

        # shop#4321: five ways of joining, two harnesses, one VS Code session
        self.session(1, 700, title="Refund round one", pr=4321)
        self.save(c(1), "round 1: refund returns 500", "-s", "fail")
        self.codex_session(csid(2), self.t(650), self.t(649), name="PR 4321 retry refund", cwd=shop)
        self.save("codex:" + csid(2), "round 2: still 500 on Codex", "-s", "fail")
        self.session(3, 600, title="Refund retry in the editor", entrypoint="claude-vscode", branch="fix/pr-4321-refund")
        self.save(c(3), "round 3: edge cases", "-s", "wip")
        self.session(4, 550, title="Refund polishing")
        self.save(c(4), "round 4: polish", "-c", "4321", "-s", "pass")
        self.session(5, 500, title="Refund wrap-up")
        self.save(c(5), "round 5: no verdict", "-c", "https://github.com/acme/Shop/pull/4321/files")

        # shop#4388: its own PR link; a second round is added below (n42)
        self.session(6, 480, title="Coupons first look", pr=4388)
        self.save(c(6), "only one round so far")

        # api#177: round 2 is deleted but kept (auto-keep on save), round 3 is deleted and was never kept
        for n, h, status in ((7, 460, "pass"), (8, 440, "fail")):
            self.session(n, h, cwd=self.api, title="API retry %d" % (n - 6))
            self.save(c(n), "api round %d" % (n - 6), "-c", "177", "-s", status)
        self.cli("config", "set", "keep.auto", "false")
        self.session(9, 420, cwd=self.api, title="API retry 3")
        self.save(c(9), "api round 3", "-c", "177", "-s", "wip")
        self.cli("config", "set", "keep.auto", "true")
        for n in (8, 9):
            self.claude_path(n).unlink()

        # a free-text chain, four spellings of one name
        self.session(10, 400, cwd=self.tracker, title="Regression sweep one")
        self.save(c(10), "sweep 1", "-c", "Release 3.2 Regression", "-s", "fail")
        self.session(11, 380, cwd=self.tracker, title="Regression sweep two")
        self.save(c(11), "sweep 2", "-c", "release 3.2    regression", "-s", "pass")

        # a session in two chains (shop#4388 and the release chain)
        self.session(42, 160, title="Coupons and the release")
        self.save(c(42), "coupons", "-c", "4388")
        self.save(c(42), "and the release", "-c", "RELEASE 3.2 regression")
        self.session(45, 150, cwd=self.tracker, title="Regression sweep four")
        self.save(c(45), "sweep 4", "-c", "Release 3.2 Regression", "-s", "fail")

        # ten rounds
        for i, status in enumerate(("fail", "fail", "wip", "fail", "wip", "pass", "fail", "wip", "pass", "pass")):
            n = 20 + i
            self.session(n, 360 - 10 * i, title="Checkout run %d" % (i + 1))
            self.save(c(n), "run %d" % (i + 1), "-c", "9000", "-s", status)

        # pinned positions: C first, then A, then B (start order would be A, B, C)
        for n, h, name, pos in ((30, 250, "Pinned A", 1), (31, 240, "Pinned B", 2), (32, 230, "Pinned C", 0)):
            self.session(n, h, title=name)
            self.link(c(n), "shop#5000", pos=pos)

        # the PR link says 4321, the title says 4400: chainlog does not guess
        self.session(40, 200, title="Refund PR 4400 check", pr=4321)
        self.last_save_40 = self.save(c(40), "looked at 4400 while testing")

        # the same number in another repository is another chain
        self.session(41, 180, cwd=self.api, title="API ticket")
        self.save(c(41), "api ticket", "-c", "4321")

        # a chain whose newest round has no verdict of its own
        self.session(43, 140, cwd=self.tracker, title="Tracker import")
        self.save(c(43), "import", "-c", "321", "-s", "blocked")
        self.session(44, 130, cwd=self.tracker, title="Tracker import, again")
        self.save(c(44), "import again", "-c", "321")

    def claude_path(self, n):
        for p in (self.home / ".claude" / "projects").glob("*/%s.jsonl" % sid(n)):
            return p
        raise KeyError(n)


_world = None


def world():
    global _world
    if _world is None:
        _world = World()
        _world.setUp()
        _world.seed()
    return _world


def tearDownModule():
    if _world is not None:
        _world.tearDown()


def ref(n):
    return "claude:" + sid(n)


class ChainJson(unittest.TestCase):
    """The Chains tab's data, `chainlog _menu`, against the seed above."""

    @classmethod
    def setUpClass(cls):
        cls.w = world()
        cls.d = cls.w.menu()
        cls.by_id = {c["id"]: c for c in cls.d["chains"]}

    def members(self, chain):
        return [m["ref"] for m in self.by_id[chain]["members"]]

    def test_every_chain_is_listed_newest_activity_first_and_counted(self):
        self.assertEqual([c["id"] for c in self.d["chains"]], CHAINS)
        self.assertEqual(self.d["counts"]["chains"], len(CHAINS))
        self.assertEqual({k for c in self.d["chains"] for k in c}, {"id", "rounds", "status", "title", "ago", "members"})

    def test_five_ways_of_joining_one_chain_across_two_harnesses_in_start_order(self):
        c = self.by_id["shop#4321"]
        self.assertEqual(self.members("shop#4321"), [ref(1), "codex:" + csid(2), ref(3), ref(4), ref(5)])
        self.assertEqual((c["rounds"], [m["round"] for m in c["members"]]), (5, [1, 2, 3, 4, 5]))
        self.assertEqual([m["harness"] for m in c["members"]], ["claude", "codex", "claude", "claude", "claude"])
        self.assertEqual([m["via"] for m in c["members"]], [None, None, "VS Code", None, None])
        sources = [next(l for l in self.w.index().sessions[m["ref"]]["chains"] if l["id"] == "shop#4321")["source"] for m in c["members"]]
        self.assertEqual(sources, ["pr-link", "title", "branch", "flag", "flag"])

    def test_the_chain_shows_the_latest_verdict_and_keeps_it_when_the_newest_round_has_none(self):
        self.assertEqual([m["status"] for m in self.by_id["shop#4321"]["members"]], ["fail", "fail", "wip", "pass", None])
        self.assertEqual(self.by_id["shop#4321"]["status"], "pass")  # round 5 has no verdict: round 4's still stands
        self.assertEqual(self.by_id["tracker#321"]["status"], "blocked")
        self.assertEqual(self.by_id["release 3.2 regression"]["status"], "fail")  # the newest verdict wins over an older pass
        self.assertEqual(self.by_id["api#177"]["status"], "wip")
        self.assertIsNone(self.by_id["shop#4388"]["status"])

    def test_the_chain_is_named_by_its_newest_round(self):
        self.assertEqual(self.by_id["shop#4321"]["title"], "Refund wrap-up")
        self.assertEqual(self.by_id["tracker#321"]["title"], "Tracker import, again")
        self.assertEqual(self.by_id["shop#9000"]["title"], "Checkout run 10")

    def test_a_chain_with_one_round_says_one(self):
        self.assertEqual(self.by_id["api#4321"]["rounds"], 1)
        self.assertEqual([m["round"] for m in self.by_id["api#4321"]["members"]], [1])

    def test_spellings_of_a_free_text_name_are_one_chain(self):
        self.assertEqual(self.members("release 3.2 regression"), [ref(10), ref(11), ref(42), ref(45)])
        self.assertNotIn("Release 3.2 Regression", self.by_id)

    def test_a_session_in_two_chains_is_listed_in_both_with_its_round_in_each(self):
        in_a = next(m for m in self.by_id["shop#4388"]["members"] if m["ref"] == ref(42))
        in_b = next(m for m in self.by_id["release 3.2 regression"]["members"] if m["ref"] == ref(42))
        self.assertEqual((in_a["chain"], in_a["round"]), ("shop#4388", 2))
        self.assertEqual((in_b["chain"], in_b["round"]), ("release 3.2 regression", 3))
        self.assertEqual(self.members("shop#4388"), [ref(6), ref(42)])
        row = next(r for r in self.d["recent"] + self.d["unsaved"] if r["ref"] == ref(42))
        self.assertEqual(row["chain"], "release 3.2 regression")  # the Sessions tab names the chain it joined last

    def test_the_sessions_tab_shows_the_same_chain_and_round_as_the_chains_tab(self):
        rows = {r["ref"]: r for r in self.d["recent"] + self.d["unsaved"]}
        checked = 0
        for c in self.d["chains"]:
            for m in c["members"]:
                if m["ref"] == ref(42):
                    continue  # in two chains: the Sessions tab names the one it joined last (checked above)
                self.assertEqual((rows[m["ref"]]["chain"], rows[m["ref"]]["round"]), (c["id"], m["round"]), m["ref"])
                checked += 1
        self.assertGreaterEqual(checked, 25)
        self.assertEqual(rows[ref(42)]["round"], 3)

    def test_ten_rounds_are_counted_but_only_the_last_eight_are_listed_with_their_real_numbers(self):
        c = self.by_id["shop#9000"]
        self.assertEqual(c["rounds"], 10)
        self.assertEqual([m["round"] for m in c["members"]], list(range(3, 11)))
        self.assertEqual([m["ref"] for m in c["members"]], [ref(n) for n in range(22, 30)])
        self.assertEqual(c["members"][-1]["title"], "Checkout run 10")

    def test_pinned_positions_beat_start_time(self):
        self.assertEqual(self.members("shop#5000"), [ref(32), ref(30), ref(31)])
        self.assertEqual([m["round"] for m in self.by_id["shop#5000"]["members"]], [1, 2, 3])
        self.assertEqual([m["saved"] for m in self.by_id["shop#5000"]["members"]], ["none"] * 3)  # joined, never saved

    def test_conflicting_evidence_joins_nothing_and_the_same_number_elsewhere_is_another_chain(self):
        for c in self.d["chains"]:
            self.assertNotIn(ref(40), [m["ref"] for m in c["members"]])
        self.assertIn("not linked", self.w.last_save_40)
        self.assertNotIn(ref(41), self.members("shop#4321"))
        self.assertEqual(self.members("api#4321"), [ref(41)])

    def test_a_deleted_round_is_restorable_when_kept_and_blocks_the_chain_when_not(self):
        rows = {m["ref"]: m for m in self.by_id["api#177"]["members"]}
        r1, r2, r3 = rows[ref(7)], rows[ref(8)], rows[ref(9)]
        self.assertEqual((r1["resume"]["restores"], r1["resume"]["blockers"]), (False, []))
        self.assertTrue(r2["resume"]["restores"])
        self.assertTrue(r2["resume"]["command"].startswith("chainlog restore %s && " % ref(8)), r2["resume"]["command"])
        self.assertEqual(r2["resume"]["blockers"], [])
        self.assertTrue(r2["kept"] and not r3["kept"])
        self.assertTrue(r3["resume"]["blockers"], "the newest round has no transcript and no copy: it cannot be opened")
        self.assertFalse(r3["resume"]["restores"])

    def test_every_member_row_has_the_same_fields_as_a_row_in_the_other_tabs_plus_its_round(self):
        other = set(self.d["recent"][0])
        for c in self.d["chains"]:
            for m in c["members"]:
                self.assertEqual(set(m), other, c["id"])
                self.assertEqual(m["chain"], c["id"])
                self.assertIsInstance(m["round"], int)

    def test_resume_commands_open_the_right_tool_in_the_right_folder(self):
        c = self.by_id["shop#4321"]["members"]
        self.assertIn("claude --resume %s" % sid(1), c[0]["resume"]["command"])
        self.assertIn("codex", c[1]["resume"]["command"])
        self.assertIn(csid(2), c[1]["resume"]["command"])
        self.assertEqual(c[0]["resume"]["cwd"], str(self.w.proj))
        api_row = self.by_id["api#4321"]["members"][0]
        self.assertEqual(api_row["resume"]["cwd"], str(self.w.api))
        self.assertTrue(c[2]["resume"]["vscode"], "the VS Code round can be reopened in the extension")
        self.assertIsNone(c[0]["resume"]["vscode"])

    def test_a_limit_of_chains_keeps_the_newest_and_the_count_is_still_the_total(self):
        short = menu.build(self.w.index(), chain_limit=3)
        self.assertEqual([c["id"] for c in short["chains"]], CHAINS[:3])
        self.assertEqual(short["counts"]["chains"], len(CHAINS))

    def test_the_sample_the_app_is_built_against_has_the_same_chain_shape(self):
        sample = json.loads((ROOT / "plugin" / "menubar" / "sample.json").read_text())
        self.assertEqual(set(sample["chains"][0]), set(self.d["chains"][0]))
        self.assertEqual(set(sample["chains"][0]["members"][0]), set(self.d["chains"][0]["members"][0]))


class ChainCommandLine(unittest.TestCase):
    """The terminal's view of the same chains must say the same things as the tab."""

    @classmethod
    def setUpClass(cls):
        cls.w = world()
        cls.d = cls.w.menu()

    def test_the_list_has_every_chain_with_its_round_count_in_the_same_order(self):
        code, out, err = self.w.cli("chain", "--json")
        self.assertEqual(code, 0, err)
        chains = json.loads(out)
        self.assertEqual([c["id"] for c in chains], CHAINS)
        self.assertEqual([len(c["members"]) for c in chains], [c["rounds"] for c in self.d["chains"]])
        code, text, _ = self.w.cli("chain")
        lines = [l for l in text.splitlines() if l.strip()]
        self.assertEqual(len(lines), len(CHAINS))
        for line, cid in zip(lines, CHAINS):
            self.assertIn(cid, line)
        self.assertIn(" 1 round ", lines[3])   # api#4321
        self.assertIn("10 rounds", lines[5])   # shop#9000

    def test_one_chain_lists_its_rounds_oldest_first_and_all_ten_of_a_long_one(self):
        code, out, _ = self.w.cli("chain", "shop#9000")
        self.assertEqual(code, 0)
        self.assertIn("10 rounds", out.splitlines()[0])
        rounds = [l for l in out.splitlines() if re.match(r"\s*@\d+ ", l) and "= round" not in l]
        self.assertEqual(len(rounds), 10)  # the terminal has no eight-round limit
        self.assertTrue(rounds[0].strip().startswith("@1") and rounds[-1].strip().startswith("@10"))
        code, out, _ = self.w.cli("chain", "shop#4321")
        lines = [l.split() for l in out.splitlines() if re.match(r"\s*@\d+ ", l) and "= round" not in l]
        shorts = [tokens[2] if tokens[1] == "▶" else tokens[1] for tokens in lines]  # a running session has a ▶ before its id
        self.assertEqual(len(shorts), 5)
        self.assertEqual([s for s in shorts[:1] + shorts[2:]], [sid(1)[:8], sid(3)[:8], sid(4)[:8], sid(5)[:8]])
        self.assertTrue(csid(2).startswith(shorts[1]) and len(shorts[1]) >= 8)

    def test_next_prints_the_command_the_tab_would_run_for_the_newest_round(self):
        for cid in ("shop#4321", "shop#9000", "release 3.2 regression", "shop#5000"):
            code, out, err = self.w.cli("next", cid, "--print")
            self.assertEqual(code, 0, err)
            newest = next(c for c in self.d["chains"] if c["id"] == cid)["members"][-1]
            self.assertEqual(out.strip(), newest["resume"]["command"], cid)

    def test_next_on_a_chain_whose_newest_round_is_gone_says_why_instead_of_opening_something_else(self):
        code, out, err = self.w.cli("next", "api#177", "--print")
        self.assertNotEqual(code, 0)
        self.assertTrue((out + err).strip())

    def test_a_round_can_be_asked_for_by_number(self):
        code, out, err = self.w.cli("next", "shop#4321/2", "--print")
        self.assertEqual(code, 0, err)
        self.assertIn(csid(2), out)

    def test_a_bare_number_means_this_repositorys_chain_and_asks_when_it_cannot_tell(self):
        code, out, _ = self.w.cli("chain", "4321", cwd=self.w.proj)
        self.assertEqual(code, 0)
        self.assertIn("shop#4321", out.splitlines()[0])
        code, out, _ = self.w.cli("chain", "4321", cwd=self.w.api)
        self.assertIn("api#4321", out.splitlines()[0])
        code, out, err = self.w.cli("chain", "4321", cwd=self.w.tracker)
        self.assertEqual(code, 2)
        self.assertIn("matches several chains", err)

    def test_ls_by_chain_and_find_agree_with_the_tab(self):
        code, out, _ = self.w.cli("ls", "--chain", "shop#9000", "--json", "-n", "50")
        self.assertEqual(len(json.loads(out)), 10)
        code, out, _ = self.w.cli("ls", "--chain", "release 3.2 regression", "--json")
        self.assertEqual({x["ref"] for x in json.loads(out)}, {ref(10), ref(11), ref(42), ref(45)})


class ChainChanges(Home):
    """Things that change a chain after the fact, each in its own small world."""

    def setUp(self):
        super().setUp()
        rt = self.make_rt(now=T0)
        cfg = config_mod.load(rt)
        cfg["installed_at"] = T0 - 2000 * H
        cfg["unsaved"]["lookback"] = 10 ** 8
        config_mod.save(rt, cfg)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.input", side_effect=[]), redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(argv), rt=self.make_rt(now=T0))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def add(self, n, start_h, chain=None, status=None, **kw):
        self.claude_session(sid(n), T0 - start_h * H, T0 - (start_h - 1) * H, title=kw.pop("title", "Work %d" % n), **kw)
        flags = (["-c", chain] if chain else []) + (["-s", status] if status else [])
        code, out, err = self.run_cli("save", "--id", ref(n), *flags, "--", "note %d" % n)
        self.assertEqual(code, 0, out + err)

    def tab(self):
        rt = self.make_rt(now=T0)
        return menu.build(load(rt, config_mod.load(rt)))

    def chains(self):
        return {c["id"]: [(m["ref"], m["round"]) for m in c["members"]] for c in self.tab()["chains"]}

    def test_hiding_a_round_renumbers_the_rest_and_hiding_them_all_removes_the_chain_and_undo_brings_it_back(self):
        for n in (1, 2, 3):
            self.add(n, 100 - n * 10, chain="shop#4321")
        self.assertEqual(self.chains(), {"shop#4321": [(ref(1), 1), (ref(2), 2), (ref(3), 3)]})
        self.run_cli("forget", ref(2))
        self.assertEqual(self.chains(), {"shop#4321": [(ref(1), 1), (ref(3), 2)]})  # the newest is now round 2
        self.run_cli("forget", ref(1))
        self.run_cli("forget", ref(3))
        self.assertEqual(self.chains(), {})
        self.assertEqual(self.tab()["counts"]["chains"], 0)
        for _ in range(3):
            self.assertEqual(self.run_cli("undo")[0], 0)
        self.assertEqual(self.chains(), {"shop#4321": [(ref(1), 1), (ref(2), 2), (ref(3), 3)]})

    def test_an_explicit_chain_replaces_a_guess_and_a_later_plain_save_does_not_add_one(self):
        self.add(1, 50, pr=4321)  # guessed from the PR link: shop#4321
        self.assertIn(ref(1), dict(self.chains()["shop#4321"]))
        self.run_cli("save", "--id", ref(1), "-c", "4400", "--", "it was 4400")
        self.assertEqual(self.chains(), {"shop#4400": [(ref(1), 1)]})  # the guess is gone
        self.run_cli("save", "--id", ref(1), "--", "one more note")
        self.assertEqual(list(self.chains()), ["shop#4400"])

    def test_two_explicit_chains_both_stay(self):
        self.add(1, 50, chain="shop#4321")
        self.run_cli("save", "--id", ref(1), "-c", "shop#4400", "--", "also this one")
        self.assertEqual(sorted(self.chains()), ["shop#4321", "shop#4400"])

    def test_unlinking_one_round_removes_only_that_round(self):
        for n in (1, 2, 3):
            self.add(n, 100 - n * 10, chain="shop#4321")
        rt = self.make_rt(now=T0)
        write_events(rt, [new_event(rt, "chain.unlink", ref(2), "cli", {"chain": "shop#4321"})])
        self.assertEqual(self.chains(), {"shop#4321": [(ref(1), 1), (ref(3), 2)]})

    def test_rounds_with_the_same_start_time_keep_one_order_whatever_order_the_files_are_found_in(self):
        for n in (1, 2, 3, 4):  # started in the same second
            self.claude_session(sid(n), T0 - 50 * H, T0 - 49 * H, title="Twin %d" % n)
            self.run_cli("save", "--id", ref(n), "-c", "shop#4321", "--", "n%d" % n)
        first = self.chains()["shop#4321"]
        import chainlog.sources.claude as claude_source
        real = os.scandir

        def reversed_scandir(path):
            with real(path) as it:
                entries = list(it)
            return _Entries(list(reversed(entries)))

        class _Entries:  # what os.scandir returns: an iterator that is also a context manager
            def __init__(self, entries):
                self._it = iter(entries)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._it)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def close(self):
                pass

        with mock.patch.object(claude_source.os, "scandir", reversed_scandir):
            other = self.chains()["shop#4321"]
        self.assertEqual(first, other, "round numbers must not swap because the disk listed the files in another order")

    def test_chain_names_with_odd_characters_stay_one_clean_line(self):
        names = {
            "ansi": "rel\x1b[31mease\x07 1",
            "bidi": "launch ‮evil‬ plan",
            "zero width": "zero​width name",
            "emoji": "🚀 launch week ✅",
            "markup": "**bold** <b>html</b> [link](http://x)",
            "long": "x" * 300,
        }
        for i, (label, name) in enumerate(names.items(), start=1):
            self.add(i, 100 - i, chain=name)
        chains = self.tab()["chains"]
        self.assertEqual(len(chains), len(names))
        for c in chains:
            for ch in c["id"]:
                self.assertNotEqual(ch, "\x1b")
                self.assertNotIn(ch, "‮‬​\x07", repr(c["id"]))  # no hidden or reordering characters in what the app shows
            self.assertEqual(c["id"], " ".join(c["id"].split()), repr(c["id"]))
        ids = {c["id"] for c in chains}
        self.assertIn("🚀 launch week ✅", ids)
        self.assertIn("**bold** <b>html</b> [link](http://x)", ids)  # shown literally: the app draws text, not markup

    def test_a_round_with_no_start_time_does_not_jump_to_round_one(self):
        for n, h in ((1, 60), (2, 50)):
            self.add(n, h, chain="shop#4321")
        # a third session whose transcript carries no timestamps at all, last worked on most recently
        path = self.home / ".claude" / "projects" / str(self.proj).replace("/", "-") / (sid(3) + ".jsonl")
        path.write_text(json.dumps({"type": "user", "sessionId": sid(3), "cwd": str(self.proj), "version": "2.1.303",
                                    "message": {"role": "user", "content": "no clock here"}}) + "\n")
        os.utime(path, (T0 - 3 * H, T0 - 3 * H))
        self.run_cli("save", "--id", ref(3), "-c", "shop#4321", "--", "n3")
        self.assertEqual([r for r, _n in self.chains()["shop#4321"]][-1], ref(3), "the most recent session is the newest round")



class ManyWorld(World):
    """Thirty-five chains (the app is sent the newest 30), with names that are long or odd, and a newest chain whose
    newest round cannot be opened."""

    NAMES = {3: "x" * 300, 4: "🚀 launch week ✅", 5: "**b** <b>x</b> [l](http://x)", 6: "a-name-that-is-exactly-thirty-six!!"}

    def seed(self):
        rt = self.make_rt(now=T0)
        cfg = config_mod.load(rt)
        cfg["installed_at"] = T0 - 2000 * H
        cfg["unsaved"]["lookback"] = 10 ** 8
        config_mod.save(rt, cfg)
        events = []
        for i in range(1, 35):
            n = i * 2 + 1
            self.claude_session(sid(n), T0 - (100 + 10 * i) * H, T0 - (99 + 10 * i) * H, title="Case %d round 1" % i)
            events.append(new_event(rt, "chain.link", "claude:" + sid(n), "cli", {"chain": self.NAMES.get(i, "shop#%d" % (1000 + i)), "source": "user"}))
        write_events(rt, events)
        # the newest chain: two saved rounds; the newest one's transcript is deleted and was never kept
        self.cli("config", "set", "keep.auto", "false")
        self.session(1, 105, title="Case 0 round 1")
        self.save("claude:" + sid(1), "first", "-c", "shop#1000")
        self.session(2, 100, title="Case 0 round 2")
        self.save("claude:" + sid(2), "second", "-c", "shop#1000")
        self.cli("config", "set", "keep.auto", "true")
        self.claude_path(2).unlink()


_many = None


def many():
    global _many
    if _many is None:
        _many = ManyWorld()
        _many.setUp()
        _many.seed()
    return _many


def tearDownModule():  # noqa: F811 - one teardown for both worlds
    for w in (_world, _many):
        if w is not None:
            w.tearDown()


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "needs macOS and swiftc")
class ChainsTabApp(unittest.TestCase):
    """What the app draws on the Chains tab (its headless --dump), against the real chainlog."""

    @classmethod
    def setUpClass(cls):
        cls._build = tempfile.TemporaryDirectory(prefix="chainlog-app-")
        p = subprocess.run(["sh", str(ROOT / "plugin" / "menubar" / "build.sh")], capture_output=True, text=True,
                           env=dict(os.environ, TMPDIR=cls._build.name), timeout=600)
        assert p.returncode == 0, p.stderr
        cls.binary = p.stdout.strip().splitlines()[-1] + "/Contents/MacOS/Chainlog"
        cls.big, cls.lots = world(), many()

    @classmethod
    def tearDownClass(cls):
        cls._build.cleanup()

    def run_app(self, w, *args):
        env = dict(os.environ, HOME=str(w.home), CHAINLOG_HOME=str(w.data), CHAINLOG_BIN=LAUNCHER, PWD=str(w.proj))
        p = subprocess.run([self.binary, *args], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def dump(self, w, *args):
        return self.run_app(w, "--dump", "--tab", "chains", *args)

    @staticmethod
    def parse(out):
        """{label: {"head": line, "continue": bool, "rounds": [(round, mark, title, detail)]}}, plus the header and footer."""
        blocks, current, header, footer = {}, None, None, None
        for line in out.splitlines():
            if line.startswith("PR chains"):
                header = line
            elif line.startswith("→ "):
                footer = line[2:]
            elif line.startswith("  → Continue the latest round"):
                current["continue"] = line.strip()
            elif line.startswith("  r"):
                m = re.match(r"^  r(\d+)  \[(.*?)\] (.*?) — (.*)$", line)
                assert m, line
                current["rounds"].append((int(m.group(1)), m.group(2), m.group(3), m.group(4)))
            elif " · " in line and not line.startswith(("tabs:", "search:", "gear:", "enter opens:", "preview:")):
                label = line.split(" · ")[0]
                current = blocks[label] = {"head": line, "continue": None, "rounds": []}
        return blocks, header, footer

    def test_every_chain_is_drawn_newest_first_with_its_rounds_newest_first(self):
        out = self.dump(self.big)
        blocks, header, footer = self.parse(out)
        self.assertEqual(header, "PR chains · 8")
        self.assertIsNone(footer)
        self.assertEqual(list(blocks), CHAINS)
        self.assertIn("Chains 8*", out.splitlines()[0])
        for label, b in blocks.items():
            rounds = [r[0] for r in b["rounds"]]
            self.assertEqual(rounds, sorted(rounds, reverse=True), label)

    def test_each_heading_says_the_verdict_and_the_number_of_rounds_in_the_right_grammar(self):
        blocks, _h, _f = self.parse(self.dump(self.big))
        heads = {k: v["head"] for k, v in blocks.items()}
        expect = {"shop#4321": ("pass", "5 rounds"), "shop#4388": ("no status", "2 rounds"), "api#4321": ("no status", "1 round"),
                  "release 3.2 regression": ("fail", "4 rounds"), "shop#9000": ("pass", "10 rounds"), "shop#5000": ("no status", "3 rounds"),
                  "api#177": ("wip", "3 rounds"), "tracker#321": ("blocked", "2 rounds")}
        for label, (status, rounds) in expect.items():
            self.assertRegex(heads[label], r"^%s · %s · %s · \d+[smhdwy]$" % (re.escape(label), status, rounds), label)
        self.assertNotIn("1 rounds", "\n".join(heads.values()))

    def test_a_long_chain_shows_its_last_eight_rounds_under_the_real_count(self):
        blocks, _h, _f = self.parse(self.dump(self.big))
        rounds = blocks["shop#9000"]["rounds"]
        self.assertEqual([r[0] for r in rounds], list(range(10, 2, -1)))
        self.assertEqual(rounds[0][2], "Checkout run 10 <pass>")  # the title and the result's pill
        self.assertEqual(blocks["shop#9000"]["continue"], "→ Continue the latest round (r10)")

    def test_continue_is_offered_only_when_the_newest_round_can_be_opened(self):
        blocks, _h, _f = self.parse(self.dump(self.big))
        for label, b in blocks.items():
            if label == "api#177":
                self.assertIsNone(b["continue"], "its newest round has no transcript and no copy")
            else:
                self.assertEqual(b["continue"], "→ Continue the latest round (r%s)" % b["head"].split(" · ")[2].split()[0], label)

    def test_a_round_that_cannot_be_opened_is_marked_and_one_that_is_restored_first_is_not(self):
        blocks, _h, _f = self.parse(self.dump(self.big))
        rounds = {r[0]: r for r in blocks["api#177"]["rounds"]}
        self.assertTrue(rounds[3][2].startswith("(can't reopen) API retry 3"), rounds[3])
        self.assertFalse(rounds[2][2].startswith("(can't reopen)"), rounds[2])
        self.assertIn("kept", rounds[2][3])  # the kept copy brings it back
        self.assertFalse(rounds[1][2].startswith("(can't reopen)"))

    def test_each_round_says_which_tool_it_ran_in_and_which_chain_it_is_listed_under(self):
        blocks, _h, _f = self.parse(self.dump(self.big))
        r = {x[0]: x for x in blocks["shop#4321"]["rounds"]}
        self.assertTrue(r[2][3].startswith("shop#4321 r2 · codex"), r[2])
        self.assertTrue(r[3][3].startswith("shop#4321 r3 · VS Code"), r[3])
        self.assertTrue(r[1][3].startswith("shop#4321 r1 · "), r[1])
        self.assertNotIn("codex", r[1][3])
        self.assertEqual([x[1] for x in blocks["shop#4321"]["rounds"]], ["saved"] * 5)
        self.assertEqual([x[1] for x in blocks["shop#5000"]["rounds"]], ["not saved"] * 3)
        both = [blocks["shop#4388"]["rounds"][0], blocks["release 3.2 regression"]["rounds"][1]]  # the session in two chains
        self.assertTrue(both[0][3].startswith("shop#4388 r2"), both[0])
        self.assertTrue(both[1][3].startswith("release 3.2 regression r3"), both[1])

    def test_enter_opens_the_newest_round_of_the_newest_chain(self):
        out = self.dump(self.big)
        self.assertIn("enter opens: Tracker import, again (r2)", out)

    def test_enter_skips_a_newest_round_that_cannot_be_opened(self):
        out = self.dump(self.lots)
        self.assertIn("  r2  [saved] (can't reopen) Case 0 round 2", out)
        self.assertIn("enter opens: Case 0 round 1 (r1)", out)  # round 2 of the newest chain is gone: the one before it

    def test_searching_the_chains_tab_looks_at_the_name_the_verdict_and_every_round_shown(self):
        def hits(*query):
            blocks, header, _f = self.parse(self.dump(self.big, "--query", " ".join(query)))
            return list(blocks), header

        self.assertEqual(hits("4321"), (["api#4321", "shop#4321"], "PR chains · 2"))
        # "release" is the chain's name and also a word in the title of one round of shop#4388
        self.assertEqual(hits("release"), (["release 3.2 regression", "shop#4388"], "PR chains · 2"))
        self.assertEqual(hits("RELEASE", "Regression"), (["release 3.2 regression"], "PR chains · 1"))  # every word, any case
        self.assertEqual(hits("polishing")[0], ["shop#4321"])     # a word from round 4's title: not the chain's own title (round 5's)
        self.assertEqual(hits("vscode")[0], ["shop#4321"])        # the round that ran in the VS Code extension
        self.assertEqual(hits("codex")[0], ["shop#4321"])
        self.assertEqual(hits("fail")[0], ["release 3.2 regression", "shop#9000", "api#177", "shop#4321"])  # any round that failed
        # tracker#321's verdict is "blocked"; shop#4321's Codex round also ended with the words "Step 3 blocked"
        self.assertEqual(hits("blocked")[0], ["tracker#321", "shop#4321"])
        self.assertEqual(hits("4321", "fail")[0], ["shop#4321"])  # words are ANDed
        self.assertEqual(hits("nothing", "like", "this")[0], [])

    def test_nothing_found_says_so_and_a_world_with_no_chains_explains_how_chains_start(self):
        out = self.dump(self.big, "--query", "zzzz-no-such-thing")
        self.assertIn("(empty: No chain matches “zzzz-no-such-thing”", out)
        self.assertNotIn("PR chains", out)

    def test_with_more_chains_than_were_sent_it_says_newest_30_of_35_and_links_to_all_of_them(self):
        blocks, header, footer = self.parse(self.dump(self.lots))
        self.assertEqual(header, "PR chains · newest 30 of 35")
        self.assertEqual(footer, "All 35 chains in Terminal…")
        self.assertEqual(len(blocks), 30)
        self.assertEqual(list(blocks)[0], "shop#1000")

    def test_searching_with_more_chains_than_were_sent_still_names_the_real_total_and_explains_the_limit(self):
        blocks, header, footer = self.parse(self.dump(self.lots, "--query", "1001"))
        self.assertEqual(list(blocks), ["shop#1001"])
        self.assertEqual(header, "PR chains · 1")
        self.assertEqual(footer, "All 35 chains in Terminal…")  # not "All 6": the total, not matches + the ones not loaded
        out = self.dump(self.lots, "--query", "1034")  # the 35th: past the 30 that were sent
        self.assertIn("(empty: ", out)
        self.assertIn("Only the newest 30 chains are loaded here", out)
        self.assertIn("All 35 chains in Terminal…", out)

    def test_long_and_odd_chain_names_are_shortened_to_one_line_and_drawn_as_plain_text(self):
        blocks, _h, _f = self.parse(self.dump(self.lots))
        labels = list(blocks)
        long = next(l for l in labels if l.startswith("xxxx"))
        self.assertEqual(long, "x" * 33 + "…")                      # 300 characters become 33 and an ellipsis
        self.assertIn("🚀 launch week ✅", labels)
        self.assertIn("**b** <b>x</b> [l](http://x)", labels)  # markup is shown as typed
        exact = next(l for l in labels if l.startswith("a-name"))
        self.assertLessEqual(len(exact), 34)
        self.assertTrue(all("\n" not in l and "\x1b" not in l for l in labels))

    def test_the_chains_tab_draws_in_light_and_dark_with_every_chain_collapsed_but_the_first(self):
        for flag in ([], ["--dark"]):
            png = Path(self._build.name) / ("chains%s.png" % ("-dark" if flag else ""))
            out = self.run_app(self.big, "--render", str(png), "--tab", "chains", *flag)
            self.assertIn("wrote", out)
            self.assertGreater(png.stat().st_size, 5000)
            self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
