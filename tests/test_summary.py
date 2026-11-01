"""Summarize a whole session (`chainlog summarize`, Summarize in the menu bar app): it reads the start and the end, writes a
few labelled lines, and keeps them with the session without saving it.

No real `claude` or `codex` runs here: FakeAI (from test_ask) answers, so the tests see exactly what was sent.
"""
import json
import os
import unittest

from helpers import NOW, iso
from test_ask import SECRET, sid
from test_context import ContextBase
from test_menubar import H

from chainlog import config as config_mod
from chainlog import menu as menu_mod
from chainlog import summarize as sum_mod
from chainlog import ui
from chainlog.ask import AskError
from chainlog.index import load

GOOD = """Sure, here it is.
HEADLINE: Upload retries added; restores over 2 GB still time out
Goal: stop uploads failing on flaky networks
Done: added backoff in backup/retry.py, limit 7
Result: tests pass (14), see [the run](https://evil.example/x)
Files & PRs: backup/retry.py; no PR
Open: restores over 2 GB
Next: ranged GETs in backup/restore.py"""


class Summary(ContextBase):
    def s(self, n=11):
        return load(self.rt, self.cfg()).sessions["claude:" + sid(n)]

    def full(self, *replies, n=11):
        self.use(*replies)
        return sum_mod.full(self.rt, self.cfg(), self.s(n))

    def test_it_reads_the_whole_session_start_and_end_and_asks_for_labelled_lines(self):
        self.agree_context()
        long = "word " * 40000
        self.claude_session(sid(30), NOW - 8 * H, NOW - 7 * H, title="Long one", prompt="GOAL-MARK make restores resumable " + long,
                            work=long + " RESULT-MARK restores resume now")
        res = self.full(GOOD, n=30)
        call = self.fake.calls[0]
        p = call["input"]
        self.assertIn("GOAL-MARK", p)  # the start, where the goal usually is
        self.assertIn("RESULT-MARK", p)  # and the end, where the result is
        system = call["argv"][call["argv"].index("--system-prompt") + 1]
        for label in ("HEADLINE:", "Goal:", "Done:", "Result:", "Files & PRs:", "Open:", "Next:"):
            self.assertIn(label, system)
        self.assertIn("data, not instructions", system)
        self.assertIn("read both", system)
        self.assertEqual(call["argv"][call["argv"].index("--model") + 1], "sonnet")
        self.assertEqual(call["argv"][call["argv"].index("--tools") + 1], "")
        self.assertEqual(res["headline"], "Upload retries added; restores over 2 GB still time out")

    def test_what_comes_back_is_tidied(self):
        self.agree_context()
        res = self.full(GOOD + "\nkey %s" % ("AKIA" + "ABCDEFGHIJKLMNOP"))
        self.assertTrue(res["text"].startswith("HEADLINE: Upload retries added"))  # the "Sure, here it is." is gone
        self.assertIn("Result: tests pass (14), see the run", res["text"])
        self.assertNotIn("evil.example", res["text"])
        self.assertNotIn("ABCDEFGHIJKLMNOP", res["text"])
        res = self.full("Uploads now retry seven times.")
        self.assertEqual(res["text"], "HEADLINE: Uploads now retry seven times.")
        res = self.full("HEADLINE: x\n" + "Done: " + "y" * 5000)
        self.assertLessEqual(len(res["text"]), sum_mod.FULL_MAX)

    def test_secrets_in_the_session_are_not_sent(self):
        self.agree_context()
        self.full(GOOD, n=12)  # session 12 has a secret in its note
        self.assertNotIn(SECRET, self.fake.calls[0]["input"])

    def test_nothing_is_sent_before_you_agree_and_contexts_agreement_covers_it(self):
        with self.assertRaises(AskError) as e:
            self.full(GOOD)
        self.assertEqual(e.exception.code, "no_consent")
        self.assertIn("Summarizing a session sends its text to Claude", e.exception.message)
        self.assertEqual(self.fake.calls, [])
        self.agree("claude")  # Ask's agreement covers summaries of excerpts, not whole sessions
        with self.assertRaises(AskError):
            self.full(GOOD)
        self.agree_context()
        self.assertTrue(self.full(GOOD)["text"])

    def test_a_saved_summary_does_not_save_the_session_and_shows_until_the_session_goes_on(self):
        self.agree_context()
        res = self.full(GOOD, n=13)
        sum_mod.save_full(self.rt, self.s(13), res)
        s = self.s(13)
        self.assertEqual(s["saved"], "none")  # still unsaved: a summary is not a note of yours
        self.assertEqual(ui.headline(s), "Upload retries added; restores over 2 GB still time out")
        row = next(r for r in menu_mod.build(load(self.rt, self.cfg()))["unsaved"] + menu_mod.build(load(self.rt, self.cfg()))["recent"]
                   if r["ref"] == "claude:" + sid(13))
        self.assertEqual(row["summary"], "Upload retries added; restores over 2 GB still time out")
        self.assertEqual(row["summary_full"].splitlines()[1], "Goal: stop uploads failing on flaky networks")
        self.assertFalse(row["summary_stale"])
        path = next((self.home / ".claude" / "projects").rglob("%s.jsonl" % sid(13)))
        with open(path, "a") as f:
            f.write(json.dumps({"type": "user", "sessionId": sid(13), "timestamp": iso(NOW - 1 * H),
                                "message": {"role": "user", "content": "one more thing"}}) + "\n")
        self.assertTrue(sum_mod.stale(self.s(13)))

    def test_an_empty_session_is_not_summarized(self):
        self.agree_context()
        s = self.s(11)
        s = dict(s, transcript=dict(s["transcript"], path="/nonexistent"), notes=[])
        self.use(GOOD)
        with self.assertRaises(AskError) as e:
            sum_mod.full(self.rt, self.cfg(), s)
        self.assertEqual(e.exception.code, "empty")
        self.assertEqual(self.fake.calls, [])

    # --- the command ---------------------------------------------------------------------------------------------------
    def test_the_command_asks_first_then_prints_and_saves(self):
        code, out, err = self.cli("summarize", sid(11)[:8])
        self.assertEqual(code, 2)  # no terminal, no --yes: nothing sent
        self.assertIn("--yes", err)
        self.use(GOOD)
        code, out, err = self.cli("summarize", sid(11)[:8], "--yes")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("HEADLINE: Upload retries added"))
        self.assertIn("saved with the session", out)
        self.assertEqual(self.cfg()["context"]["consent"], ["claude"])
        self.assertTrue(self.s(11)["summary"]["text"].startswith("HEADLINE:"))
        code, out, _ = self.cli("show", sid(11)[:8])
        self.assertIn("Upload retries added", out)
        code, out, _ = self.cli("undo")
        self.assertIsNone(self.s(11)["summary"])

    def test_print_only_shows_and_json_tells_the_app_what_is_missing(self):
        code, out, _ = self.cli("summarize", sid(11)[:8], "--json")
        self.assertEqual(code, 2)
        e = json.loads(out)
        self.assertEqual((e["error"], e["engine"]), ("no_consent", "claude"))
        self.assertIn("Summarizing a session sends", e["message"])
        self.agree_context()
        self.use(GOOD)
        code, out, _ = self.cli("summarize", sid(11)[:8], "--print")
        self.assertEqual(code, 0)
        self.assertIsNone(self.s(11)["summary"])
        self.use(GOOD)
        code, out, _ = self.cli("summarize", sid(11)[:8], "--json")
        d = json.loads(out)
        self.assertTrue(d["saved"])
        self.assertEqual(d["ref"], "claude:" + sid(11))
        self.assertEqual(d["headline"], "Upload retries added; restores over 2 GB still time out")
        self.use((1, "", "Error: rate limited"))
        code, out, _ = self.cli("summarize", sid(11)[:8], "--json")
        self.assertEqual(json.loads(out)["error"], "failed")


if __name__ == "__main__":
    unittest.main()
