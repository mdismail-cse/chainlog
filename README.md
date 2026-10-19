# chainlog

**Bookmarks and a QA chain log for your Claude Code and Codex sessions.**

You exit a session, forget to note which one it was, and the next day you can't find it, so you
start over. Your sessions were never lost; they're just unfindable. chainlog fixes that:

```console
> /exit
chainlog ▸ ended unsaved: claude 3f9a1c2e "Shop PR #1234" · 42m · shop#1234 — chainlog save "…"
$ chainlog save "r2: checkout OK, refund 500 on partial amounts" -s fail
✓ saved  claude 3f9a1c2e  "Shop PR #1234"  status fail
  chain  shop#1234 · round 2 (from pr-link)
  match  same shell (process ancestry), ended 40s ago
  resume chainlog next shop#1234   |   cd ~/code/shop && claude --resume 3f9a1c2e-…

# next day, from anywhere
$ chainlog next 1234        # opens the latest round, in the right folder, with the right tool
```

- **One command after `/exit`.** `chainlog save "note"` attaches to the session you just left (same shell, terminal tab, tmux or herdr pane). No IDs to copy.
- **Or from inside the session:** `/chainlog:save r2 refund 500 -s fail` in Claude Code, or "save this to chainlog: …" in Codex.
- **Every past session is findable on day one.** chainlog indexes the transcripts Claude and Codex already keep, read-only: `chainlog find 4321`.
- **Test chains per PR.** Rounds across Claude and Codex, in order: `chainlog chain 4321`, then `chainlog next 4321`.
- **Forgot anyway?** Your choice in settings. Ten minutes after an unsaved session ends, chainlog can auto-save it with a short summary, list it with a preview of the last work, or remind you.
- **Plays nicely with herdr.** It never edits herdr's hooks; it uses herdr's pane ID to match sessions.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/mdismail-cse/chainlog/main/install.sh | sh
```

Or from inside Claude Code:

```
/plugin marketplace add mdismail-cse/chainlog
/plugin install chainlog@chainlog
/chainlog:setup
```

Or ask your AI agent: *"Install chainlog by following https://raw.githubusercontent.com/mdismail-cse/chainlog/main/INSTALL.md"*.

`chainlog setup` shows a diff of every change and asks before each one. Codex needs one manual step afterwards: open `codex`, type `/hooks`, and trust the chainlog hook. Requires Python 3.9+, which the macOS system python already is.

## Commands

| Command | What it does |
|---|---|
| `chainlog` | Unsaved + recent sessions, numbered `@1 @2 …` |
| `chainlog save "note" [-s pass\|fail\|blocked\|wip] [-c repo#pr] [-t tag]` | Bookmark the session you just exited, or the one you're in |
| `chainlog find TEXT` · `chainlog TEXT` | Search titles, notes, PRs, chains, branches, last-work previews |
| `chainlog show REF [--copy]` | Details and the exact resume command |
| `chainlog resume REF [--fork]` · `chainlog r REF` | Resume in the session's own folder |
| `chainlog chain [KEY]` · `chainlog next KEY` | A PR's rounds in order · resume the newest round |
| `chainlog review` | Triage unsaved sessions: save with a note, auto-summary, resume or forget |
| `chainlog note/status/tag/forget REF …` · `chainlog undo` | Later corrections (`tag REF +add -remove`) · revert your last change |
| `chainlog config set unsaved.action summarize\|list\|remind\|off` | What happens to forgotten sessions |
| `chainlog doctor` · `chainlog uninstall [--purge]` | Health checks · clean removal (byte-identical restore) |

A **REF** can be an ID prefix (`3f9a1c`), `@3` from the last list, `last`, a chain (`shop#4321`, `4321`, `4321/2`), or a Codex thread name.

## Unsaved sessions: your choice

```sh
chainlog config set unsaved.action summarize   # 1: auto-save with a short summary (◐)
chainlog config set unsaved.action list        # 2: show them with "last ask / last work" previews (default)
chainlog config set unsaved.action remind      # 3: desktop + herdr notification + a terminal line
chainlog config set unsaved.after 10m
chainlog config set summary.engine local       # local (free, offline) | auto (the session's own AI)
```

For `summarize` and `remind`, setup offers a small background sweep (a launchd agent on macOS, a systemd user timer on Linux) that checks every 2 minutes, so it happens on time even when no terminal is open. Sessions that ended before you installed chainlog are never touched. A session you resume and end again gets a fresh summary.

AI summaries are opt-in. They send only a short, redacted excerpt of the conversation text (never tool output) to the same provider that ran the session. They leave no transcript and fire no hooks.

## Privacy

chainlog reads transcripts read-only and stores only metadata, your notes and 160-character redacted previews, in `~/.local/share/chainlog` (mode 0700). It never opens `*.key`, `~/.codex/auth.json` or `~/.claude/history.jsonl`. It never writes under `~/.claude` or `~/.codex` outside `setup`, and every setup write is backed up and shown to you first. See [SECURITY.md](SECURITY.md).

## License

MIT
