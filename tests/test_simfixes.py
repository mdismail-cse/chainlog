"""Regression tests for the findings of the 8-agent simulation run."""
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from helpers import NOW, ROOT, Home

from chainlog import cli, ui
from chainlog import config as config_mod
from chainlog import sweep
from chainlog.index import load, write_events
from chainlog.integrations import base
from chainlog.integrations import components as comp
from chainlog.noteargs import parse
from chainlog.query import find
from chainlog.redact import redact
from chainlog.resolve import which
from chainlog.store.events import new_event, read_events

A = "aaaa1111-0000-0000-0000-000000000001"
B = "bbbb2222-0000-0000-0000-000000000002"


def run_cli(rt, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv, rt=rt)
    return code, out.getvalue(), err.getvalue()


class TerminalEscapes(Home):
    def test_titles_and_previews_are_printable(self):
        evil = "PR 1 \x1b]52;c;ZXZpbA==\x07 \x1b[2J \x1b]0;pwned\x07 done\nnext"
        self.claude_session(A, NOW - 3000, NOW - 2000, title=evil, work=evil)
        rt = self.make_rt()
        for argv in (["ls"], ["show", A[:8]], ["find", "PR"]):
            _c, out, _e = run_cli(rt, argv)
            self.assertNotIn("\x1b", out, argv)
            self.assertNotIn("\x07", out, argv)
        self.assertEqual(ui.safe("a\x1b[2Jb\nc"), "a [2Jb c")


class SupersededSession(Home):
    def test_killed_newer_session_in_same_shell_is_not_skipped(self):
        self.claude_session(A, NOW - 3000, NOW - 300, title="PR 7001 login r1")
        self.spool_end("claude", A, NOW - 300, anc=[11, 4242])
        rt = self.make_rt(ppid=4242)
        write_events(rt, [new_event(rt, "save", "claude:" + A, "cli", {"note": "r1 done"})])
        self.claude_session(B, NOW - 200, NOW - 30, title="PR 7001 login r2")
        self.spool_end("claude", B, NOW - 200, anc=[12, 4242], event="start")  # killed: no end hook
        kind, c = which(load(rt))
        self.assertEqual(kind, "many")
        self.assertEqual(c[0]["session"]["native_id"], B)  # the newer one is offered first


class SkillDelimiter(unittest.TestCase):
    def test_no_fixed_heredoc_delimiter(self):
        for p in ("plugin/skills/save/SKILL.md", "plugin/codex/skills/chainlog-save/SKILL.md"):
            text = (ROOT / p).read_text()
            self.assertNotIn("CHAINLOG_NOTE_8f2c71", text, p)
            self.assertIn("random", text, p)
            self.assertIn("code 2", text, p)


class TornEventLine(Home):
    def test_next_save_survives_a_torn_line(self):
        rt = self.make_rt()
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "events.jsonl").write_text('{"v":1,"id":"x","ts":1,"type":"save","ref":"claude:' + A)
        ev = new_event(rt, "note", "claude:" + A, "cli", {"text": "kept"})
        write_events(rt, [ev])
        self.assertEqual([e["id"] for e in read_events(rt)], [ev["id"]])


class OddTranscriptShapes(Home):
    def test_unexpected_json_types_do_not_crash(self):
        p = self.claude_session(A, NOW - 3000, NOW - 2000, title="ok title")
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "message": "a plain string"}) + "\n")
            f.write(json.dumps({"type": "custom-title", "customTitle": 42}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": "not a list"}}) + "\n")
            f.write('{"type": "user", "message": {"content": "caf\\udce9"}}\n')
        idx = load(self.make_rt())
        self.assertEqual(idx.sessions["claude:" + A]["title"]["value"], "ok title")


class TagRemoval(Home):
    def test_minus_tag_removes(self):
        self.claude_session(A, NOW - 3000, NOW - 2000)
        rt = self.make_rt()
        self.assertEqual(run_cli(rt, ["tag", A[:8], "+smoke", "+ui"])[0], 0)
        self.assertEqual(run_cli(rt, ["tag", A[:8], "-ui"])[0], 0)
        self.assertEqual(load(rt).sessions["claude:" + A]["tags"], ["smoke"])


class ChainKeys(Home):
    def test_bare_number_uses_own_repo_and_url_forms(self):
        kaz = self.root / "work" / "tracker"
        (kaz / ".git").mkdir(parents=True)
        (kaz / ".git" / "config").write_text('[remote "origin"]\n\turl = git@github.com:acme/Tracker.git\n')
        self.claude_session(A, NOW - 9000, NOW - 8000, title="Shop PR 4321", pr=4321)
        self.claude_session(B, NOW - 3000, NOW - 2000, title="dashboard", cwd=kaz)
        rt = self.make_rt()
        run_cli(rt, ["save", "--id", A[:8], "x r1"])
        run_cli(rt, ["save", "--id", B[:8], "kaz", "-c", "4321"])
        self.assertIn("tracker#4321", load(rt).chains)
        from chainlog.chain import normalize_key, parse_ref
        self.assertEqual(normalize_key("https://github.com/acme/Shop/pull/4321"), "shop#4321")
        self.assertEqual(normalize_key("acme/Shop#4321"), "shop#4321")
        self.assertEqual(parse_ref("#4321"), (None, 4321, None))

    def test_no_auto_chain_from_folder_name(self):
        loose = self.root / "work" / "scratch"
        loose.mkdir(parents=True)
        self.claude_session(A, NOW - 3000, NOW - 2000, title="PR 7001 login", cwd=loose)  # no git remote
        rt = self.make_rt()
        run_cli(rt, ["save", "--id", A[:8], "note"])
        self.assertEqual(load(rt).sessions["claude:" + A]["chains"], [])


class TranscriptMoves(Home):
    def test_archived_codex_session_stays_resumable(self):
        sid = "01a1aaaa-0000-0000-0000-000000000003"
        p = self.codex_session(sid, NOW - 3000, NOW - 2000)
        rt = self.make_rt()
        write_events(rt, [new_event(rt, "save", "codex:" + sid, "cli", {"note": "r1"})])
        load(rt)
        arch = self.home / ".codex" / "archived_sessions" / p.name
        arch.parent.mkdir(parents=True)
        shutil.move(str(p), str(arch))
        s = load(rt).sessions["codex:" + sid]
        self.assertTrue(s["transcript"]["present"])
        self.assertTrue(s["transcript"]["archived"])

    def test_deleted_transcript_and_full_scan_keep_the_bookmark(self):
        p = self.claude_session(A, NOW - 3000, NOW - 2000, title="Shop PR 4321 refund", pr=4321)
        rt = self.make_rt()
        run_cli(rt, ["save", "--id", A[:8], "r1"])
        os.unlink(p)
        run_cli(rt, ["scan", "--full"])
        s = load(rt).sessions["claude:" + A]
        self.assertEqual(s["title"]["value"], "Shop PR 4321 refund")
        self.assertFalse(s["transcript"]["present"])
        self.assertTrue(s["cwd"])


class UniqueShortIds(Home):
    def test_uuid7_like_ids_get_distinct_shorts(self):
        ids = ["01a2b3c4-7918-7aa0-b147-4a09bb6f549c", "01a2b3c4-7a40-7aa0-b147-4a09bb6f549d"]
        for i, sid in enumerate(ids):
            self.codex_session(sid, NOW - 3000 + i, NOW - 2000 + i)
        shorts = [s["short"] for s in load(self.make_rt()).visible()]
        self.assertEqual(len(set(shorts)), 2)


class HerdrFileStyle(Home):
    def test_compact_and_non_ascii_preserved(self):
        compact = '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"bash \'/x/herdr – ñ.sh\' session"}]}]}}'
        (self.home / ".codex" / "hooks.json").write_text(compact)
        rt = self.make_rt()
        base.apply(rt, comp.plan_codex(rt))
        after = (self.home / ".codex" / "hooks.json").read_text()
        self.assertIn("herdr – ñ.sh", after)  # no \\u escapes
        self.assertNotIn("\n", after)  # still compact
        self.assertEqual(comp.remove_codex_entry(after), compact)

    def test_hook_command_quoted_and_stub_baked(self):
        rt = self.make_rt(env={"CHAINLOG_HOME": str(self.root / "my data")})
        changes = comp.plan_codex(rt)
        base.apply(rt, changes)
        cmd = json.loads((self.home / ".codex" / "hooks.json").read_text())["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
        self.assertIn("'%s'" % (self.root / "my data" / "hook" / "spool.sh"), cmd)
        self.assertIn("my data/spool", (self.root / "my data" / "hook" / "spool.sh").read_text())


class SetupContract(Home):
    def test_json_stdout_is_pure_and_unknown_parts_refused(self):
        rt = self.make_rt()
        code, out, _err = run_cli(rt, ["setup", "codex", "policy", "--dry-run", "--json", "--policy", "remind"])
        doc = json.loads(out)
        self.assertEqual(doc["schema"], "chainlog.setup/1")
        self.assertIn("scheduler", [c["component"] for c in doc["components"]])  # dry run shows the timer
        code, _o, _e = run_cli(rt, ["setup", "all", "--skip", "clauude", "--yes"])
        self.assertEqual(code, 2)


class PolicyResumedSession(Home):
    def test_active_again_is_not_due_and_refreshes_after_next_end(self):
        rt = self.make_rt()
        cfg = config_mod.load(rt)
        cfg = config_mod.set_value(cfg, "unsaved.action", "summarize")
        cfg["installed_at"] = NOW - 86400
        config_mod.save(rt, cfg)
        self.claude_session(A, NOW - 9000, NOW - 120)  # resumed: active 2 min ago
        self.spool_end("claude", A, NOW - 3000, anc=[11])  # first end, 50 min ago
        sweep.run(load(rt, cfg), mode="cron")
        self.assertEqual(load(rt, cfg).sessions["claude:" + A]["saved"], "none")
        later = self.make_rt(now=NOW + 1200)
        self.spool_end("claude", A, NOW + 300, anc=[11])  # ended again
        os.utime(next((self.home / ".claude" / "projects").rglob(A + ".jsonl")), (NOW + 300, NOW + 300))
        sweep.run(load(later, cfg), mode="cron")
        self.assertEqual(load(later, cfg).sessions["claude:" + A]["saved"], "auto")


class NoteFlags(unittest.TestCase):
    def test_flag_like_text_stays_in_the_note(self):
        self.assertEqual(parse("then curl -s https://x")["note"], "then curl -s https://x")
        self.assertEqual(parse("done -s PASS")["status"], "pass")
        self.assertEqual(parse("check -t 2")["tags"], [])


class FindNaturalPhrase(Home):
    def test_filler_words_ignored(self):
        self.claude_session(A, NOW - 3000, NOW - 2000, title="Shop PR 4321 refund flow")
        hits = find(load(self.make_rt()), "the session where I tested the refund for 4321")
        self.assertEqual([s["native_id"] for s in hits], [A])


class MiscFixes(Home):
    def test_config_bools_strict_and_bearer_redacted(self):
        cfg = config_mod.load(self.make_rt())
        with self.assertRaises(ValueError):
            config_mod.set_value(cfg, "snippets", "flase")
        self.assertEqual(config_mod.set_value(cfg, "snippets", "off")["snippets"], False)
        self.assertNotIn("abcdefghijklmnop", redact("Authorization: Bearer abcdefghijklmnopqrstu")[0])


if __name__ == "__main__":
    unittest.main()
