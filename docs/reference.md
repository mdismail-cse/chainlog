# chainlog reference

Everything chainlog can do, in detail. For a gentle introduction, start with the [README](../README.md).

- [All commands at a glance](#all-commands-at-a-glance)
- [Pointing at a session (REF)](#pointing-at-a-session-ref)
- [save](#save) · [list and ls](#list-and-ls) · [find](#find) · [show](#show) · [resume and next](#resume-and-next) · [chain](#chain)
- [keep, retention, delete](#keep-retention-delete) · [note, status, tag, forget, undo](#note-status-tag-forget-undo) · [review](#review) · [config](#config)
- [setup](#setup) · [doctor, scan, uninstall](#doctor-scan-uninstall)
- [Settings](#settings) · [Environment variables](#environment-variables) · [Exit codes](#exit-codes)
- [How chainlog picks the session](#how-chainlog-picks-the-session) · [The forgot-to-save rules](#the-forgot-to-save-rules)
- [How it works](#how-it-works) · [Development](#development)

---

## All commands at a glance

| Command | What it does |
|---|---|
| `chainlog` | Unsaved + recent sessions, numbered `@1 @2 …` |
| `chainlog save ["note"] [options]` | Bookmark the session you just left (or the one you're in) |
| `chainlog find WORDS` / `chainlog WORDS` | Search everything |
| `chainlog ls [filters]` | List sessions with filters |
| `chainlog show REF` | Everything about one session + its resume command |
| `chainlog resume REF` / `chainlog r REF` | Reopen a session in its folder |
| `chainlog chain [KEY]` | All chains, or one chain's rounds |
| `chainlog next KEY` | Reopen the newest round of a chain |
| `chainlog note REF "text"` | Add a note |
| `chainlog status REF STATUS` | Set the status: `wip`, `pass`, `fail` or `blocked` |
| `chainlog tag REF +add -remove` | Add or remove tags |
| `chainlog star REF…` | Star sessions you want to keep close (favourites); `chainlog unstar REF…` takes them off |
| `chainlog keep REF…` | Keep a copy Claude's cleanup cannot delete (`unkeep` drops it, `ls --kept` lists them) |
| `chainlog retention [DAYS]` | Show or set how long Claude keeps transcripts (`cleanupPeriodDays`) |
| `chainlog delete REF…` | Delete sessions from Claude, from chainlog, or both |
| `chainlog forget REF` | Hide a session from lists |
| `chainlog undo` | Revert your last save / note / status / tag / star / forget |
| `chainlog review` | Go through unsaved sessions one by one |
| `chainlog ask [QUESTION]` | Ask Claude or Codex about your sessions (connect once with `--connect`) |
| `chainlog config …` | Show or change settings |
| `chainlog setup [PART…]` | Install integrations (asks first) |
| `chainlog doctor` | Health check with fixes |
| `chainlog uninstall` | Remove integrations (keeps your notes) |
| `chainlog scan` | Re-index transcripts |

Every command has `--help`.

## Pointing at a session (REF)

| You type | Means |
|---|---|
| `9b8c7d6e` | ID prefix, as shown in lists (any unique prefix of 4+ characters) |
| `@3` | Row 3 of the last list you printed (`chainlog`, `ls`, `find` or `chain KEY`) |
| `last` | The session that ended most recently |
| `4321`, `shop#4321`, `#4321` | The newest round of that PR's chain |
| `4321/2` | Round 2 of that chain |
| `"PR 4321 retest after the fix"` | A Codex thread name |

## save

```sh
chainlog save "note" [-s STATUS] [-c CHAIN] [-t TAG] [--id REF | --pick] [--url URL] [--title TEXT] [--no-chain] [--json]
```

| Option | Meaning |
|---|---|
| `"note"` | What you tested and what happened. Secrets such as tokens and passwords are redacted automatically |
| `-s, --status` | `wip`, `pass`, `fail` or `blocked` |
| `-c, --chain` | Put it in a chain: `4321` (this repo's PR 4321), `shop#4321`, `owner/shop#4321` or a PR URL |
| `--no-chain` | Don't link a chain automatically |
| `-t, --tag` | Add a tag (repeatable): `-t smoke -t mobile` |
| `--id REF` | Save a specific session instead of the one just exited |
| `--pick` | Choose from your recent sessions |
| `--url URL` | Attach a link, e.g. a claude.ai/code session or a ticket |
| `--title TEXT` | Your own title (otherwise Claude's or Codex's title is used) |
| `--json` | Machine-readable output |

- Saving the same session again **adds** another note.
- An explicit `-c` replaces a chain that was linked automatically.
- Notes up to 8,000 characters are kept.

## list and ls

`chainlog` on its own shows **Unsaved** sessions plus the 15 most recent:

<img src="images/list.svg" alt="The chainlog session list">

| Icon | Meaning |
|---|---|
| `●` | saved by you |
| `◐` | auto-saved with a summary |
| `○` | not saved yet |
| `▶` | still running |

In a terminal, the list ends with a prompt:

| You type | What happens |
|---|---|
| A number | Opens that session (like `chainlog resume @N`) |
| Words or a PR number | Searches, then shows new numbered results |
| Enter | Quits |

`find`, `ls` and `chain KEY` end with the same prompt.
Scripts, pipes, `--json` and AI agents never get it; set `CHAINLOG_NO_PROMPT=1` to turn it off.

`chainlog ls` adds filters:

```sh
chainlog ls --chain 4321        # one chain
chainlog ls --pr shop#4321      # anything mentioning that PR
chainlog ls --here              # this repo only (all its clones)
chainlog ls --since 2d          # last 2 days (90m, 12h, 7d …)
chainlog ls --harness codex     # only Codex (or claude)
chainlog ls --via vscode        # only sessions started in the VS Code extension (or desktop, terminal)
chainlog ls --unsaved           # only sessions without a note
chainlog ls --saved -n 50       # only saved ones, up to 50 rows
chainlog ls --starred           # only your starred sessions (★ in every list)
chainlog ls --all               # include empty sessions (opened, nothing typed)
chainlog ls --json              # machine-readable
```

## find

```sh
chainlog find refund coupon     # or just: chainlog refund coupon
```

`find` searches:
- your notes, titles and chains;
- PR numbers and branches;
- folder names;
- the last thing you asked and the AI answered.

Results are ranked by how many of your words match, and filler words such as "the session where I tested" are ignored. `-n` sets the number of rows; `--json` gives machine-readable output.

<img src="images/find.svg" alt="Searching sessions by PR number">

## show

<img src="images/show.svg" alt="chainlog show">

`--copy` copies the resume command to the clipboard; `--json` gives machine-readable output.
`show` also warns when Claude is about to delete the transcript.

## resume and next

```sh
chainlog resume @3              # reopen a session (alias: chainlog r @3)
chainlog next 4321              # reopen the newest round of a chain
chainlog next 4321/2            # reopen round 2
chainlog resume @3 --fork       # branch off into a new session instead of continuing
chainlog resume @3 --print      # only print the command
chainlog resume @3 --here       # run from the current folder instead of the session's
```

chainlog changes into the session's folder, then runs `claude --resume <id>` or `codex resume <id>`.
- **Still running:** it warns before opening a second copy.
- **Folder gone:** use `--here`.
- **Codex session archived:** it tells you how to unarchive it.
- **Claude deleted it but you kept a copy:** the copy is put back first (`chainlog restore REF` does only that).

## keep, retention, delete

Claude Code deletes a session's transcript when the file's last-modified time is older than `cleanupPeriodDays`
(its default is 30). The Claude desktop app has its own, shorter rule for sessions you archive or delete there.

```sh
chainlog keep @3 @5              # copy sessions into chainlog's folder (compressed, about a sixth of the size)
chainlog keep --refresh          # bring the copies up to date after a session grew (the menu bar app and the sweep do this)
chainlog unkeep @3               # delete the copy (the original is untouched; asks when the copy is all that is left)
chainlog ls --kept               # what you keep
chainlog restore @3              # put a copy back where Claude looks (resume does this itself)

chainlog retention               # how long Claude keeps sessions now, and what expires within 14 days
chainlog retention 180           # set cleanupPeriodDays to 180 (1-3650): shows the change, asks, backs the old file up
chainlog retention --default     # remove it, so Claude uses 30 again

chainlog delete @3               # from Claude (transcript + its sub-agent folder) and chainlog (copy, hidden from lists)
chainlog delete @3 --from claude # only Claude's file: chainlog's copy stays, so it can still be reopened
chainlog delete @3 --from chainlog  # only chainlog's side: Claude's file stays (chainlog undo brings the listing back)
```

- **Automatic:** saving a session with a note, or starring it, keeps a copy (`chainlog config set keep.auto false` turns that off).
  Sessions chainlog auto-saves are not kept.
- **A copy holds the whole conversation, unredacted** (anything you pasted is in it, as in the original). It sits in
  `~/.local/share/chainlog/kept/claude/`, readable only by you. Only Claude sessions are kept; Codex does not delete its own.
- **Restoring** writes the file back with a fresh modification time, so Claude's cleanup does not take it again at once.
- **`retention` lowering the number** warns how many sessions Claude would delete the next time it starts, and how many of
  those have no kept copy.
- **`delete`** refuses a running session, asks first (`--yes` skips that), and only removes
  `~/.claude/projects/<project>/<session id>.jsonl` and the `<session id>/` folder beside it. Small per-session leftovers Claude
  keeps elsewhere (file history, to-dos, your prompt history) are left to Claude's own cleanup. chainlog's log of events is
  append-only, so a deleted session's notes stay in it; `chainlog uninstall --purge` removes all of chainlog's data.

## ask

```sh
chainlog ask "which sessions touched the refund PR, and how did each end?"
chainlog ask -e codex "what did I do last week?"   # this question to the other tool (you must have connected it)
chainlog ask --json "…"                            # {answer, sessions[], engine, model, calls, listed}
chainlog ask                                       # in a terminal: a small chat; an empty line ends it
chainlog ask --status [--json]                     # what is installed, signed in and connected
chainlog ask --connect claude|codex [--yes]        # shows what is sent, asks, then connects
chainlog ask --disconnect                          # take your agreement back
```

It runs the `claude` or `codex` you are signed in to (`claude -p`, `codex exec`): no keys, no account of its own.

**What it sends.** The question, the last few messages of the chat, and a catalog of your sessions as text: one line each with the
short id, date, tool, chain and round, result, title, your note, the first ask and last thing done, branch, PR numbers and tags.
The newest 250 sessions are listed, plus every starred or saved one and the ones matching the question's words, up to 70,000
characters (the oldest go first). Everything goes through the same secret redaction as notes and previews, and `<` / `>` in session
text are replaced so it cannot close or open the tags around it. The model can ask for more: `NEED: id…` returns the detail of up to 6 sessions
(everything you asked in them, in order, your notes, and the assistant's last few replies, 5,500 characters each), and
`SEARCH: words` returns matches from all sessions. That makes at most 3 requests per question; the last one must answer.

**How it is run.** From an empty temporary folder, with no tools and no user config:
`claude -p --no-session-persistence --tools "" --strict-mcp-config --setting-sources project --model sonnet` and
`codex exec --ephemeral --ignore-user-config --disable shell_tool --disable plugins --disable apps --disable hooks -s read-only`.
Without those flags `claude -p` can read files in its folder and use every connector on your account (Docs, Slack, …), and
`codex exec -s read-only` can still run shell commands. The chat leaves no session behind in your list. The same flags are used for
AI-written summaries. `Stop` in the app ends the AI process too.

**The answer.** `[[id]]` marks a session; each one is checked against your own sessions before it is shown (an unknown id is dropped),
and a link the AI writes is turned into plain text, so no link in an answer can open anything but one of your sessions.

**Connecting** (`--connect`) shows the text above, asks, then records your agreement in `config.json`
(`ask.engine`, `ask.consent`). Asking with a tool you have not agreed to is refused. Without a terminal `--connect` needs `--yes`.
The settings `ask.model` (default `sonnet` for Claude; Codex uses its own default) and `ask.timeout` (seconds per request, default 90)
are in [Settings](#settings).

## chain

```sh
chainlog chain           # all chains
chainlog chain 4321      # one chain, every round
```

<img src="images/chain.svg" alt="A chain with four rounds">

A session joins a chain:
1. with `-c` when you save;
2. via `CHAINLOG_CHAIN=shop#4321` set before starting the session;
3. automatically, when Claude recorded a PR link, or when the title says "PR 4321" and the folder is a git repo.

- **Chain key:** `<repo name>#<PR number>`, where the repo name comes from the git remote. So clones and worktrees share one chain.
- **Rounds:** ordered by start time.
- **Chain status:** the latest round's status.

## note, status, tag, forget, undo

```sh
chainlog note @2 "retested on Firefox too"
chainlog status 4321 pass                 # the newest round of chain 4321
chainlog tag @2 +firefox -smoke
chainlog forget @5                        # hide from lists
chainlog undo                             # revert the last change of these kinds, or a save
```

## review

Goes through unsaved sessions one by one. For each you can:

| Key | Action |
|---|---|
| `s` | save with a note (you can add `-s fail` and so on) |
| `a` | auto-summary |
| `r` | resume |
| `f` | forget |
| `n` | next |
| `q` | quit |

`--all` includes sessions from before you installed chainlog. Without a terminal, `review` prints JSON.

`--auto` saves **every** unsaved session with an automatic summary (the `a` key, for all of them). It asks first
in a terminal (Enter = yes; `--yes` skips the question). Sessions get their PR chain when the evidence is clear;
a session whose PR link and title name different PRs is saved but left unlinked, and the line says why.

<img src="images/review.svg" alt="chainlog review">

## config

```sh
chainlog config                           # all settings
chainlog config get unsaved.action
chainlog config set unsaved.action remind
```

## setup

```sh
chainlog setup                            # everything; asks per part
chainlog setup codex shell                # only these parts
chainlog setup all --dry-run              # show every change, apply nothing
chainlog setup all --yes --policy list    # no questions (scripts / agents, after the user agreed)
```

| Part | Installs |
|---|---|
| `cli` | The `chainlog` command in `~/.local/bin` |
| `claude` | The Claude Code plugin (hooks, `/chainlog:save`, `/chainlog:find`, `/chainlog:setup`) |
| `codex` | One `SessionEnd` hook in `~/.codex/hooks.json` and two Codex skills. Afterwards: open `codex`, type `/hooks`, approve it |
| `shell` | One marked block in `~/.zshrc` (the "ended unsaved" hint, plus `~/.local/bin` on your PATH if needed) |
| `policy` | The forgot-to-save question |
| `scheduler` | A 2-minute background timer for `summarize`/`remind` (a launchd agent, or a systemd timer on Linux) |
| `menubar` | The Mac menu bar app: `~/Applications/Chainlog.app`, built from `plugin/menubar/Chainlog.swift` on your machine (needs `swiftc`: `xcode-select --install`) |

- `all` leaves out `scheduler` and `menubar`; name them to get them.
- `--skip PART` leaves a part out.
- Every edited file is backed up, and existing files keep their formatting.
- Without a terminal and without `--yes`, setup only shows the plan.

## Menu bar app (macOS)

`chainlog setup menubar` builds and installs `~/Applications/Chainlog.app` (ad-hoc signed, no Dock icon) and opens it.
Run it again after updating chainlog and it rebuilds the app; `chainlog uninstall` quits and removes it.
Click the icon to open the popover.

| In the popover | What it does |
|---|---|
| **Search** | Searches every session (not only the listed ones) for titles, your notes, PR numbers, branches, folders and what was said, best match first (`chainlog _menu --find`). Enter opens the first match; Esc clears it, then closes. In the Starred tab it only shows starred matches; in Chains it filters the chains it was sent (the newest 30) by name, result and the title, result and tool of every round shown |
| **Sessions** tab | Its number is every session chainlog lists. **Unsaved · N · last 24 h** (the 8 newest, then "Review all N unsaved…", which runs `chainlog review` in a terminal): only sessions that ended within the unsaved window (`unsaved.lookback`, 24 h) and after chainlog was installed count as unsaved. Then **Everything else**: all the other sessions, newest first (up to 300; beyond that it says how many are not shown, and search finds them). A saved session shows its result as a pill (pass, fail, wip, blocked) |
| **Starred** tab | Every starred session. Starring is `chainlog star` / `unstar`, so the terminal and the app always agree |
| **Chains** tab | The newest 30 PR chains (the header says "newest 30 of N", with "All N chains in Terminal…" which runs `chainlog chain`); the newest starts expanded, with "Continue the latest round" and every round. Which chains you expanded is remembered. Enter opens the newest round of the newest chain |
| Hover a session | The panel at the bottom shows it at once: its title, the last thing said in it, and its folder (or why it can't be reopened). Moving over the list redraws only that panel |
| Click a session | Opens it in a new window of the terminal you picked, in its own folder (the same command as `chainlog resume`). Where `chainlog resume` would ask first (the session is running now, an archived Codex session, an unknown folder) the app asks too, and Cancel is the default |
| Hover → copy button, ⌥-click, or right-click → Copy command | Copies the command instead of running it: `cd FOLDER && claude --resume ID` (or `codex resume ID`), or `chainlog restore REF && …` first when Claude deleted the original and a kept copy exists. Paste it into any terminal. Works on search results. Not offered for a session that can't be reopened |
| Hover → link button, or right-click → Copy VS Code link | For a Claude session started in the VS Code extension: copies its `vscode://anthropic.claude-code/open?session=ID` link (not offered when the transcript is gone or has to be restored first: the link would not bring it back) |
| Right-click → Copy session id | Copies the id `claude --resume` / `codex resume` take, for any session |
| Right-click → Keep a copy / Stop keeping the copy | `chainlog keep` / `unkeep`. A kept session says "kept" on its second line; if Claude deleted the original, clicking it still opens it from the copy. The app also refreshes kept copies at launch and every 20 minutes |
| Right-click → Delete… | `chainlog delete` after a confirmation: from Claude and chainlog, only from Claude, or only from chainlog (Codex sessions: chainlog only). A running session is refused |
| Right-click → Open in VS Code | For a Claude session started in the VS Code extension (its row says "VS Code"): opens its folder in VS Code, then the session in the extension's own panel, through the extension's `vscode://anthropic.claude-code/open?session=ID` link. The terminal command in a plain click works for these sessions too (they share Claude's session store), this is for staying in the editor |
| Hover: ☆ / ★ | Star or unstar. Shown at once; if `chainlog` refuses, it flips back and says why |
| Hover: pencil | Save the session with a note and a result: `chainlog save --id REF [-s STATUS] [--chain=NAME] -- NOTE`. The "Add to a chain" line takes any name (a PR number works too) or one picked from the chains that exist; a chain alone is enough to save. A note or chain name that starts with a dash stays text. What you type is kept if the popover closes, and given back if the save fails |
| **Ask** tab | A chat with Claude or Codex about your sessions (see [ask](#ask)). Until you connect, it lists which of the two are installed and signed in, with a Connect button that first shows what is sent. Then: suggestions, your messages, answers (a session it names is a link that opens it, and the sessions it mentions are listed under it as ordinary rows with copy, star and note), a copy button for the answer, **Stop** while it works, **New chat**, and ⋯ → *Use the other tool* / *What is sent* / *Disconnect*. The chat is kept while the app runs, also when the popover is closed. Return sends |
| ⚙ → **Open sessions in** | Terminal (default), iTerm2 (if installed) or "Only copy the command". Remembered |
| ⚙ → **Open at login** | Starts the app when you log in |
| ⚙ → **Keep a copy when I save or star** | The `keep.auto` setting (`chainlog config set keep.auto …`) |
| ⚙ → **Claude keeps sessions for N days…** | Asks for a number of days (1–3650, starting from today's), checks with `chainlog retention N --json` what that would delete, asks again if it would delete anything (and says how many have no kept copy), then runs `chainlog retention N --yes`. A line under it warns when sessions with no kept copy expire within 14 days (nothing is shown when they are all kept) |

The popover opens under the icon you clicked, on the display you clicked it on, and slides to stay on that display. (On
macOS 26 a menu bar item's own window has no screen, so the popover is anchored under the click, not to the item.)

A session whose transcript Claude Code already deleted is shown dimmed and cannot be reopened.
The app refreshes when you open it, every minute (one `chainlog` call at a time), and after the Mac wakes. A failed
refresh keeps the last list and says so. It finds `chainlog` in `~/.local/bin`, `/opt/homebrew/bin` or `/usr/local/bin`;
set `CHAINLOG_BIN` to use another one. Terminal windows start an interactive login shell, so `claude` and `codex`
are found on the same PATH as in your own Terminal.

Debugging and tests: `CHAINLOG_MENUBAR_DEBUG=1` prints what the app does to stderr. `Chainlog --dump [FILE]
[--tab starred] [--query TEXT]` prints what the popover shows, as text, without opening a window; `--render OUT.png
[--dark]` draws it to an image; `--act star|unstar|save …` does what a button does; `--hover-first` shows the preview panel for the first row (`CHAINLOG_MENUBAR_ANCHOR=x,y` pretends the click was at
that screen point); `--selftest SAMPLE.json` runs the
model's own checks; `--unregister` removes the app from Login Items (`chainlog uninstall` does this before deleting
it). `sh plugin/menubar/build.sh --check` builds and checks all of it; `--shots DIR` redraws the README images from
the sample data.

## doctor, scan, uninstall

- `chainlog doctor` checks every part and prints the fix for anything missing. Use `--json` for scripts.

  <img src="images/doctor.svg" alt="chainlog doctor">
- `chainlog scan` re-indexes transcripts. This happens automatically; `--full` re-reads everything.
- `chainlog uninstall` removes the hooks, skills, zsh block and timer, and keeps your notes. `--purge` also deletes chainlog's data.
  Edited files are restored byte-for-byte. If you edited a file since, only chainlog's own entry is removed.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `unsaved.action` | `list` | `summarize`, `list`, `remind` or `off` |
| `unsaved.after` | `10m` | How long after a session ends before the policy acts (1m–24h) |
| `unsaved.lookback` | `1d` | Sessions that ended longer ago are never acted on automatically |
| `summary.engine` | `local` | `local` (free, offline), or `auto`, `claude` or `codex` (AI-written; `auto` = the tool that ran the session) |
| `summary.timeout` | `60` | Seconds to wait for an AI summary before falling back to `local` |
| `snippets` | `true` | Store short, redacted previews of the last ask and last answer, which `find` searches |
| `auto_chain` | `true` | Link saves to a PR chain automatically (never when the PR link and the title name different PRs) |
| `hints` | `true` | One-line tips under the list, such as "Claude Code isn't connected". Only shown in a terminal |
| `keep.auto` | `true` | Keep a copy of every session you save or star, so Claude's cleanup cannot delete it |
| `ask.model` | *(empty)* | Model for `chainlog ask`, e.g. `sonnet` or `opus`; empty = `sonnet` for Claude, Codex's own default |
| `ask.timeout` | `90` | Seconds one request to Claude / Codex may take (10–600) |

## Environment variables

| Variable | Effect |
|---|---|
| `CHAINLOG_CHAIN=shop#4321` | Set before starting a session to put it in that chain |
| `CHAINLOG_HOME` | Where chainlog keeps its data (default `~/.local/share/chainlog`) |
| `CHAINLOG_NO_PROMPT=1` | Lists never ask "type a number to open it" |
| `NO_COLOR` | Colours off |
| `CHAINLOG_COLOR=always` | Colours on even when piped |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | OK |
| `1` | Error |
| `2` | "Ask the user first": an ambiguous session (the candidates are printed as JSON), or setup without consent |

## How chainlog picks the session

```mermaid
flowchart TD
    S["chainlog save 'note'"] --> Q1{"Running inside a<br/>Claude/Codex session?"}
    Q1 -->|yes| E1["✓ that session (exact)"]
    Q1 -->|no| Q2{"A session ended in this<br/>shell · pane · tab?"}
    Q2 -->|yes| Q2b{"Did a newer session also run<br/>here without a clean exit?"}
    Q2b -->|no| E2["✓ save it, and show why"]
    Q2b -->|yes| P["? asks you, newest first"]
    Q2 -->|no| Q3{"Ended in this repo<br/>in the last 30 min?"}
    Q3 -->|exactly one| E3["✓ save it, marked guessed,<br/>with an undo hint"]
    Q3 -->|several| P
    Q3 -->|none| P2["? pick from recent sessions"]
```

- Panes from herdr, tmux and iTerm are told apart, so parallel sessions in one window don't get mixed up.
- When run by a script or an AI, chainlog never asks interactively: it exits 2 with the candidates as JSON.

## The forgot-to-save rules

A session is acted on only when **all** of these are true:
- it ended after you installed chainlog;
- it had at least one prompt;
- it isn't saved;
- it isn't running;
- it's not a sub-agent;
- `unsaved.after` has passed since it ended (30 minutes of inactivity if the tool gave no end signal).

Each session is handled once. A resumed-and-ended-again session gets a fresh turn, and an auto-summary is refreshed. Your own note always wins.

## How it works

```mermaid
flowchart LR
    CC["Claude Code"] -- "session start / end" --> HK["tiny hook script<br/>(shell only, ~0.05 s)"]
    CX["Codex"] -- "session end" --> HK
    HK --> ST[("~/.local/share/chainlog<br/>events + index")]
    TR[("Transcripts Claude and Codex<br/>already keep on disk")] -.->|read-only| CLI
    ST <--> CLI["chainlog command"]
    CLI --> U["save · find · chain · resume"]
```

- **The hook** records which terminal, pane and shell a session ran in. It never starts Python.
- **The `chainlog` command** reads those records and the transcripts (read-only), and works out titles, PR links, branches and previews.
- **Your saves, notes and statuses** go into an append-only log. Formats: [SCHEMA.md](../SCHEMA.md). Safety: [SECURITY.md](../SECURITY.md).

## Development

```sh
git clone https://github.com/mdismail-cse/chainlog && cd chainlog
make test                     # tests (Python 3.9+, no dependencies)
python3 docs/screenshots.py   # regenerate the screenshots from real output
CHAINLOG_SOURCE=$PWD sh install.sh --no-setup   # install your checkout
```
