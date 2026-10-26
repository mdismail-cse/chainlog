"""Starred (favourite) sessions, `_menu --find`, and the `save` call the menu bar app makes."""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import NOW, Home

from chainlog import cli
from chainlog import config as config_mod
from chainlog.index import load
from chainlog.store.events import read_events

H = 3600
A = "aaaa1111-0000-4000-8000-000000000001"
B = "bbbb2222-0000-4000-8000-000000000002"
C = "cccc3333-0000-4000-8000-000000000003"


class StarHome(Home):
    def setUp(self):
        super().setUp()
        self.rt = self.make_rt()
        cfg = config_mod.load(self.rt)
        cfg["installed_at"] = NOW - 200 * H
        config_mod.save(self.rt, cfg)
        self.claude_session(A, NOW - 30 * H, NOW - 29 * H, title="Shop PR 4321 refund", pr=4321)
        self.claude_session(B, NOW - 5 * H, NOW - 4 * H, title="Tracker CSV export", work="Export drops the last row")
        self.claude_session(C, NOW - 3 * H, NOW - 2 * H, title="Shop PR 4388 coupons", work="Coupons stack twice")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv), rt=self.rt)
        return code, out.getvalue(), err.getvalue()

    def session(self, sid):
        return load(self.rt).sessions["claude:" + sid]

    def menu(self, *extra):
        code, out, _e = self.run_cli("_menu", *extra)
        self.assertEqual(code, 0)
        return json.loads(out)


class StarCommands(StarHome):
    def test_star_and_unstar(self):
        code, out, _e = self.run_cli("star", A[:8])
        self.assertEqual(code, 0)
        self.assertIn("starred", out)
        s = self.session(A)
        self.assertTrue(s["starred"])
        self.assertEqual(s["saved"], "none")  # starring is not saving: it stays in "unsaved"
        self.run_cli("unstar", A[:8])
        self.assertFalse(self.session(A)["starred"])

    def test_starring_twice_writes_nothing_new(self):
        self.run_cli("star", A[:8])
        n = len(read_events(self.rt))
        code, out, _e = self.run_cli("star", A[:8])
        self.assertEqual(code, 0)
        self.assertIn("already starred", out)
        self.assertEqual(len(read_events(self.rt)), n)
        code, out, _e = self.run_cli("unstar", B[:8])
        self.assertIn("already unstarred", out)

    def test_several_sessions_at_once_and_by_list_number(self):
        self.run_cli("ls")  # @1 = newest = C, @2 = B, @3 = A
        code, out, _e = self.run_cli("star", "@1", B[:8], "@1")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("starred"), 2)  # @1 given twice is only starred once
        self.assertTrue(self.session(C)["starred"] and self.session(B)["starred"] and not self.session(A)["starred"])

    def test_ls_starred_and_the_star_in_the_list_and_in_show(self):
        self.run_cli("star", B[:8])
        code, out, _e = self.run_cli("ls", "--starred")
        self.assertEqual(code, 0)
        self.assertIn("Tracker CSV export", out)
        self.assertNotIn("PR 4321", out)
        self.assertIn("★ ", out)
        _c, shown, _e = self.run_cli("show", B[:8])
        self.assertIn("★ starred", shown)
        code, out, _e = self.run_cli("ls", "--starred", "--json")
        self.assertEqual([x["short"] for x in json.loads(out)], [B[:8]])
        self.assertTrue(json.loads(out)[0]["starred"])

    def test_an_empty_ls_starred_says_so(self):
        code, out, _e = self.run_cli("ls", "--starred")
        self.assertEqual(code, 1)
        self.assertIn("no sessions match", out)

    def test_undo_takes_the_star_back(self):
        self.run_cli("star", A[:8])
        code, out, _e = self.run_cli("undo")
        self.assertEqual(code, 0)
        self.assertIn("undid star", out)
        self.assertFalse(self.session(A)["starred"])

    def test_unknown_session_is_an_error_and_forgotten_ones_stay_out(self):
        code, _o, err = self.run_cli("star", "deadbeef")
        self.assertEqual(code, 1)
        self.assertIn("no session matches", err)
        self.run_cli("star", A[:8])
        self.run_cli("forget", A[:8])
        self.assertEqual(self.menu()["counts"]["starred"], 0)

    def test_a_starred_session_without_a_prompt_is_still_listed(self):
        # Claude "opened and quit" sessions are hidden from lists; one you starred is on purpose
        sid = "dddd4444-0000-4000-8000-000000000004"
        path = self.claude_session(sid, NOW - 9000, NOW - 8000, title="Empty one", prompt="")
        self.assertNotIn("claude:" + sid, [s["ref"] for s in load(self.rt).visible()])
        code, _o, _e = self.run_cli("star", "claude:" + sid)
        self.assertEqual(code, 0)
        self.assertIn("claude:" + sid, [s["ref"] for s in load(self.rt).visible()])
        path.unlink()


class MenuStarred(StarHome):
    def test_rows_carry_the_flag_and_the_starred_list_is_all_of_them(self):
        self.run_cli("star", A[:8])
        self.run_cli("star", C[:8])
        d = self.menu()
        self.assertEqual(d["counts"]["starred"], 2)
        self.assertEqual([r["short"] for r in d["starred"]], [C[:8], A[:8]])  # newest activity first
        everywhere = d["unsaved"] + d["recent"] + d["starred"]
        self.assertTrue(all(r["starred"] for r in d["starred"]))
        self.assertEqual({r["short"] for r in everywhere if r["starred"]}, {A[:8], C[:8]})
        self.assertFalse(next(r for r in everywhere if r["short"] == B[:8])["starred"])

    def test_starred_beyond_the_listed_rows_are_still_in_the_starred_list(self):
        for i in range(12):
            sid = "%08x-5555-4000-8000-%012x" % (i, i)
            self.claude_session(sid, NOW - (60 + i) * H, NOW - (59 + i) * H, title="Old session %d" % i)
            self.run_cli("star", sid[:8])
        d = self.menu()
        self.assertEqual(d["counts"]["starred"], 12)
        self.assertEqual(len(d["starred"]), 12)
        listed = {r["ref"] for r in d["unsaved"] + d["recent"]}
        self.assertTrue(any(r["ref"] not in listed for r in d["starred"]))


class MenuFind(StarHome):
    def test_find_searches_every_session_not_just_the_listed_ones(self):
        for i in range(30):
            sid = "%08x-6666-4000-8000-%012x" % (i, i)
            self.claude_session(sid, NOW - (100 + i) * H, NOW - (99 + i) * H, title="Filler %d" % i)
        self.claude_session("eeee5555-0000-4000-8000-000000000005", NOW - 400 * H, NOW - 399 * H,
                            title="Needle in the haystack", work="Found the zebra bug")
        d = self.menu("--find", "zebra")
        self.assertEqual(d["query"], "zebra")
        self.assertEqual([r["title"] for r in d["results"]], ["Needle in the haystack"])
        self.assertNotIn("Needle in the haystack", [r["title"] for r in d["unsaved"] + d["recent"]])

    def test_results_are_best_match_first_with_the_star_flag(self):
        self.run_cli("star", A[:8])
        d = self.menu("--find", "refund")
        self.assertEqual(d["results"][0]["short"], A[:8])
        self.assertTrue(d["results"][0]["starred"])
        self.assertEqual(set(d["results"][0]), {"ref", "short", "harness", "title", "saved", "status", "live", "starred",
                                                "chain", "round", "at", "ago", "last_work", "resume"})

    def test_no_match_and_blank_queries_give_an_empty_list_not_an_error(self):
        for q in ("qqqqzzzz", "", "   "):
            d = self.menu("--find", q)
            self.assertEqual(d["results"], [], repr(q))

    def test_without_find_there_are_no_results_keys(self):
        d = self.menu()
        self.assertNotIn("results", d)
        self.assertNotIn("query", d)

    def test_the_search_sample_has_the_same_row_shape(self):
        from helpers import ROOT
        sample = json.loads((ROOT / "plugin" / "menubar" / "sample-find.json").read_text())
        live = self.menu("--find", "refund")
        self.assertEqual(set(sample), {"schema", "query", "results"})
        self.assertEqual(set(sample["results"][0]), set(live["results"][0]))
        self.assertEqual(set(sample["results"][0]["resume"]), set(live["results"][0]["resume"]))


class SaveFromTheApp(StarHome):
    """The app saves with: chainlog save --id <full ref> [-s STATUS] -- <note>   (no shell, no tty)."""

    def test_a_note_that_starts_with_a_dash_or_looks_like_a_flag_is_kept_verbatim(self):
        for note in ("-s fail looks like a flag", "--help me", "-1 degree"):
            code, out, err = self.run_cli("save", "--id", "claude:" + B, "--", note)
            self.assertEqual(code, 0, err)
        notes = [n["text"] for n in self.session(B)["notes"] if n["kind"] == "note"]
        self.assertEqual(notes, ["-s fail looks like a flag", "--help me", "-1 degree"])
        self.assertIsNone(self.session(B)["status"])

    def test_status_flag_before_the_separator_sets_status(self):
        self.run_cli("save", "--id", "claude:" + B, "-s", "fail", "--", "export drops a row")
        s = self.session(B)
        self.assertEqual((s["status"], s["saved"]), ("fail", "user"))

    def test_saving_does_not_depend_on_which_folder_it_runs_in_and_links_the_pr(self):
        code, out, _e = self.run_cli("save", "--id", "claude:" + A, "--", "round 1 ok")
        self.assertEqual(code, 0)
        self.assertEqual([c["id"] for c in self.session(A)["chains"]], ["shop#4321"])

    def test_saving_a_starred_session_keeps_the_star(self):
        self.run_cli("star", B[:8])
        self.run_cli("save", "--id", "claude:" + B, "--", "checked")
        s = self.session(B)
        self.assertTrue(s["starred"] and s["saved"] == "user")


if __name__ == "__main__":
    unittest.main()
