"""Regression tests for the code-review findings (one class per finding)."""
import io
import json
import os
import subprocess
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import NOW, ROOT, Home

from chainlog import cli
from chainlog import config as config_mod
from chainlog import setup as setup_mod
from chainlog import sweep
from chainlog.index import load, write_events
from chainlog.integrations import base
from chainlog.integrations import components as comp
from chainlog.resolve import which
from chainlog.store import spool
from chainlog.store.events import new_event, read_events

A = "aaaa1111-0000-0000-0000-000000000001"
B = "bbbb2222-0000-0000-0000-000000000002"


class Args:
    def __init__(self, **kw):
        self.__dict__.update(dict(purge=False, yes=True, components=[], dry_run=False, skip=[], policy=None,
                                  engine=None, local=False, json=False))
        self.__dict__.update(kw)


def run_cli(rt, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv, rt=rt)
    return code, out.getvalue(), err.getvalue()


class F1UntrustedFallbackSpool(Home):
    def test_shared_dir_is_ignored_and_never_followed(self):
        rt = self.make_rt()
        fb = spool.fallback_dir(rt)
        fb.mkdir(parents=True, mode=0o755)
        os.chmod(fb, 0o755)  # e.g. pre-created by another user in a shared /tmp
        evil = new_event(rt, "save", "claude:" + A, "cli", {"note": "injected"})
        (fb / "1-1-x.event.json").write_text(json.dumps(evil))
        victim_dir = self.root / "victim"
        victim_dir.mkdir()
        victim = victim_dir / "old.txt"
        victim.write_text("keep me")
        os.utime(victim, (NOW - 30 * 86400, NOW - 30 * 86400))
        os.symlink(victim_dir, fb / "quarantine")
        load(rt)
        self.assertEqual([e for e in read_events(rt) if e["id"] == evil["id"]], [])
        self.assertTrue(victim.exists())
        with self.assertRaises(PermissionError):
            spool.write_fallback_event(rt, evil)

    def test_own_private_fallback_is_ingested(self):
        rt = self.make_rt()
        (self.root / "tmp").mkdir()
        ev = new_event(rt, "note", "claude:" + A, "skill:codex", {"text": "from sandbox"})
        path = spool.write_fallback_event(rt, ev)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        load(rt)
        self.assertEqual([e["id"] for e in read_events(rt)], [ev["id"]])


class F2CrossPane(Home):
    def test_pane_mismatch_vetoes_shared_tab(self):
        self.claude_session(A, NOW - 3000, NOW - 300)
        self.claude_session(B, NOW - 3000, NOW - 60)
        tab = {"term": "w0t0p0:TAB"}
        self.spool_end("claude", A, NOW - 300, anc=[11], fp=dict(tab, herdr_pane="w5:p1"))
        self.spool_end("claude", B, NOW - 60, anc=[22], fp=dict(tab, herdr_pane="w5:p2"))
        rt = self.make_rt(env={"TERM_SESSION_ID": "w0t0p0:TAB", "HERDR_PANE_ID": "w5:p1"})
        kind, c = which(load(rt))
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("strong", A))

    def test_tmux_percent_and_linux_tty(self):
        self.claude_session(A, NOW - 3000, NOW - 60)
        self.spool_end("claude", A, NOW - 60, anc=[11], fp={"tmux": "3"})  # spool.sh strips '%'
        kind, c = which(load(self.make_rt(env={"TMUX_PANE": "%3"})))
        self.assertEqual(kind, "strong")
        self.claude_session(B, NOW - 3000, NOW - 30)
        self.spool_end("claude", B, NOW - 30, anc=[12], tty="pts/3")
        kind, c = which(load(self.make_rt(tty_name="pts/3")))
        self.assertEqual(c[0]["session"]["native_id"], B)


class F3InsideSessionWithoutId(Home):
    def test_codex_skill_never_lands_on_an_ended_claude_session(self):
        self.claude_session(A, NOW - 3000, NOW - 120)
        self.spool_end("claude", A, NOW - 120, anc=[11], fp={"term": "TAB"})
        cdx = "01a1cccc-0000-0000-0000-000000000003"
        self.codex_session(cdx, NOW - 600, NOW - 20)  # running: rollout written 20 s ago
        rt = self.make_rt(env={"TERM_SESSION_ID": "TAB"},
                          proctable={4242: (3000, "bash"), 3000: (1, "codex")})
        kind, c = which(load(rt), via="skill:codex")
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("strong", cdx))

    def test_no_running_session_means_ask(self):
        self.claude_session(A, NOW - 3000, NOW - 120)
        self.spool_end("claude", A, NOW - 120, anc=[11], fp={"term": "TAB"})
        kind, _c = which(load(self.make_rt(env={"TERM_SESSION_ID": "TAB"})), via="skill:codex")
        self.assertIn(kind, ("many", "none"))


class F4EmptyFollowUp(Home):
    def test_prefers_the_session_with_work(self):
        self.claude_session(A, NOW - 3000, NOW - 300)
        self.spool_end("claude", A, NOW - 300, anc=[11, 4242])
        self.spool_end("claude", B, NOW - 60, anc=[12, 4242])  # opened and quit at once: no transcript
        kind, c = which(load(self.make_rt(ppid=4242)))
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("exact", A))


class F5ConcurrentSweeps(Home):
    def test_two_sweeps_act_once(self):
        rt = self.make_rt()
        cfg = config_mod.load(rt)
        cfg = config_mod.set_value(cfg, "unsaved.action", "summarize")
        cfg = config_mod.set_value(cfg, "summary.engine", "claude")
        cfg["installed_at"] = NOW - 86400
        config_mod.save(rt, cfg)
        self.claude_session(A, NOW - 4000, NOW - 900)
        self.spool_end("claude", A, NOW - 900, anc=[11])

        def slow_claude(argv, timeout=5.0, input=None, env=None, cwd=None):
            if argv[0] == "claude":
                time.sleep(0.6)
                return 0, "tested refund; 500 on coupons; next: tax rounding", ""
            return 1, "", ""
        rt._run = slow_claude
        threads = [threading.Thread(target=lambda: sweep.run(load(rt, cfg), mode="cron")) for _ in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        saves = [e for e in read_events(rt) if e["type"] == "save"]
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0]["data"]["summary_engine"], "claude")


class F6NoConsentWithoutTty(Home):
    def test_setup_without_tty_changes_nothing(self):
        (self.home / ".zshrc").write_text("# mine\n")
        rt = self.make_rt(tty=False)
        code = setup_mod.run(rt, Args(components=["shell"], yes=False), lambda *a: None)
        self.assertEqual(code, 2)
        self.assertEqual((self.home / ".zshrc").read_text(), "# mine\n")

    def test_uninstall_without_tty_needs_yes(self):
        rt = self.make_rt(tty=False)
        base.apply(rt, comp.plan_shell(rt))
        self.assertEqual(setup_mod.uninstall(rt, Args(yes=False, purge=True), lambda *a: None), 2)
        self.assertTrue(self.data.exists())


class F7DotfileIntegrity(Home):
    def test_symlink_mode_and_raw_bytes_survive(self):
        dot = self.root / "dotfiles"
        dot.mkdir()
        target = dot / "zshrc"
        target.write_bytes(b"export X=1 # caf\xe9 latin-1\n")
        os.chmod(target, 0o644)
        os.symlink(target, self.home / ".zshrc")
        rt = self.make_rt()
        before = target.read_bytes()
        base.apply(rt, comp.plan_shell(rt))
        self.assertTrue(os.path.islink(self.home / ".zshrc"))
        self.assertIn(comp.MARK_BEGIN.encode(), target.read_bytes())
        self.assertTrue(target.read_bytes().startswith(before))
        setup_mod.uninstall(rt, Args(), lambda *a: None)
        self.assertTrue(os.path.islink(self.home / ".zshrc"))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(oct(os.stat(target).st_mode & 0o777), "0o644")


class F8ManifestAndEdges(Home):
    def test_conflict_keeps_what_was_written_undoable(self):
        (self.home / ".codex" / "hooks.json").write_text("{}")
        rt = self.make_rt()
        changes = comp.plan_codex(rt)
        skill = changes[-1]
        skill.path.parent.mkdir(parents=True, exist_ok=True)
        skill.path.write_text("someone else's skill")  # changes under our feet after planning
        with self.assertRaises(base.ConflictError):
            base.apply(rt, changes)
        written = [e for e in base.load_manifest(rt)["entries"] if e.get("path", "").endswith("hooks.json")]
        self.assertEqual(len(written), 1)

    def test_empty_hooks_json(self):
        (self.home / ".codex" / "hooks.json").write_text("")
        changes = comp.plan_codex(self.make_rt())
        self.assertTrue(any(c.path and c.path.name == "hooks.json" and not c.noop for c in changes))

    def test_missing_end_marker_keeps_rest_of_file(self):
        text = "a\n%s\n[[ -r x/chainlog.zsh ]] && source x/chainlog.zsh\nb\nc\n" % comp.MARK_BEGIN
        self.assertEqual(comp.remove_shell_block(text), "a\nb\nc\n")


class F9Chains(Home):
    def test_bare_number_joins_repo_chain_and_chain_ref_is_exact(self):
        self.claude_session(A, NOW - 90000, NOW - 89000, title="Shop PR 4321 checkout", pr=4321)
        self.claude_session(B, NOW - 3000, NOW - 2000, title="misc")
        rt = self.make_rt(tty=False)
        run_cli(rt, ["save", "--id", A[:8], "r1"])
        code, out, _ = run_cli(rt, ["save", "--id", B[:8], "r2", "-c", "4321"])
        idx = load(rt)
        self.assertEqual([m["ref"] for m in idx.chains["shop#4321"]["members"]], ["claude:" + A, "claude:" + B])
        write_events(rt, [new_event(rt, "chain.link", "claude:" + B, "cli", {"chain": "shop#1234"})])
        code, out, _ = run_cli(rt, ["chain", "shop#4321"])
        self.assertTrue(out.startswith("shop#4321"), out)


class F10DueTimes(Home):
    def run_stub(self, sid, env=None):
        e = dict(os.environ, CHAINLOG_HOME=str(self.data), HOME=str(self.home))
        e.update(env or {})
        subprocess.run(["sh", str(ROOT / "plugin" / "hooks" / "spool.sh"), "claude", "end"],
                       input=json.dumps({"session_id": sid}).encode(), env=e, capture_output=True)

    def test_hook_keeps_the_earlier_due(self):
        (self.data / "spool").mkdir(parents=True)
        far = int(time.time()) + 7200
        (self.data / "spool" / ".due").write_text("%d\n" % far)
        self.run_stub(A)
        due = int((self.data / "spool" / ".due").read_text())
        self.assertLess(due, far)

    def test_list_policy_under_cron_keeps_due_now(self):
        rt = self.make_rt()
        cfg = config_mod.load(rt)
        cfg["installed_at"] = NOW - 86400
        config_mod.save(rt, cfg)
        self.claude_session(A, NOW - 4000, NOW - 900)
        self.spool_end("claude", A, NOW - 900, anc=[11])
        sweep.run(load(rt, cfg), mode="cron")
        self.assertIsNotNone(spool.pending_due(rt))
        self.assertLessEqual(spool.pending_due(rt), NOW)
        self.assertTrue(sweep.run(load(rt, cfg), mode="tty"))  # the prompt still shows it


class F11FriendlyErrors(Home):
    def test_no_tracebacks(self):
        self.claude_session(A, NOW - 3000, NOW - 2000, title="Shop PR 4321", pr=4321)
        rt = self.make_rt(tty=False)
        self.assertEqual(run_cli(rt, ["ls", "--pr", "shop#4321"])[0], 0)
        code, _o, err = run_cli(rt, ["ls", "--since", "1w"])
        self.assertEqual(code, 1)
        self.assertIn("not a duration", err)
        code, _o, err = run_cli(rt, ["config", "get", "nope"])
        self.assertEqual(code, 1)


class F12FolderGuess(Home):
    def test_home_does_not_match_subfolders(self):
        self.claude_session(A, NOW - 3000, NOW - 120)  # in self.proj (a git repo under the tmp root)
        kind, _c = which(load(self.make_rt(cwd=self.root)))
        self.assertEqual(kind, "none")
        sub = self.proj / "src" / "deep"
        sub.mkdir(parents=True)
        kind, c = which(load(self.make_rt(cwd=sub)))  # same git work tree
        self.assertEqual((kind, c[0]["session"]["native_id"]), ("weak", A))


if __name__ == "__main__":
    unittest.main()
