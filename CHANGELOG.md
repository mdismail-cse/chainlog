# Changelog

## Unreleased

- Contexts: the notes record facts, not activity. The per-session reader is told that "ran the tests", "committed", "pushed"
  and "opened a PR" on their own are not facts (a test run counts with its command and result, a PR by its number and what
  it contained), to prefer the exact thing over the verb ("retry limit 3 → 7 in backup/retry.py upload()", paths, settings,
  commands, test-site URLs and error messages spelled as recorded), to note a decision only with its reason and the option
  rejected, and to answer in the questions' language. The merge used to say "record only what happened, in your own words",
  which paraphrased exact commands away; it now keeps paths, names, commands, numbers and error messages exactly as the notes
  give them and drops bullets that are only a routine step. Commands that only look around (`ls`, `cat`, `git status`,
  `git diff`…) are no longer listed under [commands run].
- An automatic one-line summary no longer poses as the whole-session one, and never replaces it. The local summary (the
  facts the preview shows already, restated) is not shown as a summary line any more; an AI one-liner is shown as the
  line but the app offers *Summarize the whole session* instead of *Show its summary*; and when the sweep comes by a session
  you already summarized it keeps that summary (and makes no request) instead of overwriting it with a one-liner.
  Automatic summaries now record how long the session was, so they too show as out of date when it went on.
- Summaries and contexts no longer read chainlog's own old summary as if you wrote it. The facts sent with a session gave
  the latest summary's headline as the session's *title* and the whole summary as one of your *notes*, so "Summarize again"
  and every context build anchored on the stale text and repeated it. Now the title is the real title, the notes are yours
  only, a new summary reads the session alone, and a context build sees the old summary once, labelled as chainlog's own
  older output (and as written before the session went on, when it was), with the rule that the conversation wins.
- Summaries carry usable facts, not activity. The whole-session summary has two new lines, **Decisions** (each choice with
  its reason, and what was tried and dropped) and **Gotchas** (what broke first and the fix: the exact error, the setting or
  file at fault, environment facts, a command that only works one way); the prompt now bans process noise (commits, pushes,
  PRs opened, "ran the tests" without the result, files only read), the title said again in the headline, and invented PR
  numbers; a label with nothing to say ("Open: none") is dropped instead of shown. The automatic one-line summary gets the
  same rules and now sees the session's title (it used to come back as the title restated), and its text is fenced and
  escaped the way contexts' is, so a message in it cannot pose as instructions.
- Contexts: **Open** on every built context in the Chains tab opens its notes in an editor (the one you picked last; its menu
  lists every editor installed, each session's notes and Show in Finder), and `chainlog context open NAME` opens them in your
  text editor. The only way was a right-click item that used the app your Mac opens `.md` files with, which can be a
  terminal or a browser.
- Your own instructions for every summary: `chainlog summarize --instructions "…"` (setting `summary.instructions`) and ⚙ →
  Summary instructions… in the app; `--with "…"` / right-click → Summarize with instructions… for one. The automatic
  (forgot-to-save) summaries follow them too, and now read a session's first messages as well as its last ones.
- Summarize a whole session: `chainlog summarize REF`, and a summary button on every session in the menu bar app. It reads
  the start and the end of the session (where the goal and the result are), the files it changed and the commands it ran,
  and writes a headline plus Goal, Done, Result, Files & PRs, Open and Next. Kept with the session without saving it, shown
  under the row and in the preview, marked out of date when the session went on, `chainlog undo` takes it back. On a
  made-up session whose last 30 messages were small renames, the old one-line summary only saw the renames; this one had
  the goal, the 3.1 TB move, the fix, the PR and what is left.
- Menu bar app, Sessions: the hover preview shows the whole arc, not only the end: where the session started, the last thing
  said, your latest note (else its summary), and its result, PR or branch, lines changed and length. The local summary
  also says where a session started.
- Contexts: a focus. `--focus "…"` (and the Focus field in step 2 of the builder; a description you searched with becomes the
  focus): each session is read only for what concerns it, other work in it is left out, and the notes and the reference say
  the focus. Changing it reads every session again. On made-up mixed-topic sessions with the real Claude: no unrelated facts
  left, every relevant one kept, notes 17% shorter.
- Contexts: find the sessions by describing the work. `chainlog context suggest "…"`, `context new NAME --describe "…"`, and
  *Describe it (AI)* in the app's builder: one request with Ask's short summaries (not whole conversations) to the tool
  connected in Ask; the recommended sessions come grouped by chain or PR, marked strong or maybe with the reason, and a
  suggested name; Pick strong, Pick all, or per group.
- Contexts: ready-made question sets (Bug fix, Feature, PR review / QA testing, Investigation, and chainlog's ten):
  `--preset` on `context new`, `context update` (new questions read every session again) and `context questions`. The app's
  builder is three steps (Sessions, Questions, Build) with the questions always in sight, *Start from* a set, and *Edit
  questions…* on every context.
- Menu bar app, Ask tab: the tool in use is a menu at the top (*Claude · sonnet ▾*) to switch to the other one, see what is
  sent, or disconnect. It was hidden in a ⋯ button, and the other tool was only offered after a status check.
- Contexts: merge what many sessions taught you into one small file a new session reads only when it needs it.
  `chainlog context new "Cloud backup" --find backup` (or ids, or `--chain KEY`) asks each session your questions (ten ready-made
  ones: the problem, root cause, fix, how it works, testing, risks, what is open, decisions, facts, what next), one request
  per session, then merges the answers: `CONTEXT.md` (gist, In short, contents with line numbers, a section per question,
  timeline, every fact citing its session) and `sessions.md`. `chainlog context use NAME [task]` starts Claude or Codex with
  only a few lines pointing to it (about 200 tokens; it reads the file when the task needs it), `ref --copy` gives those lines
  for a running session, `update` reads only new and changed sessions (and the sessions started from the context, and new rounds
  of a chain it follows). In the menu bar app: Contexts in the Chains tab, a builder with a session picker and editable
  questions, progress with Stop, Start in Claude/Codex, Copy reference, and "Add to a context" on every session. Its own
  agreement before whole sessions are sent; settings `context.model` and `context.timeout`.
- Search: `_menu --find TEXT --every-word` (and `context … --find`) keeps only sessions that contain every word.

- Ask Claude or Codex about your sessions: `chainlog ask "which sessions touched the refund PR, and how did each end?"`, and an **Ask**
  tab in the menu bar app (connect screen, chat, the sessions an answer mentions as rows you can open or copy, Stop, New chat). It uses
  the `claude` / `codex` you are signed in to, after you agree to what is sent (`chainlog ask --connect`). Everything sent is redacted;
  the AI gets no tools and runs in an empty folder; links in an answer never work; a session it names is checked against your list.
  When the answer needs more it asks for the detail of a few sessions or a search, up to 3 requests per question.
  `chainlog ask --status`, `--disconnect`, `-e codex`, `--json`; settings `ask.model` and `ask.timeout`.
- Security: the AI summaries (`summary.engine claude|codex`) were run in a way that let the AI read files and, for Claude, use every
  connector of your account (Docs, Slack, …), and let Codex run shell commands, so text recorded in an old session could steer it.
  Both now run with no tools and no user config (`--tools "" --strict-mcp-config`; `--ignore-user-config --disable shell_tool …`).
  `chainlog` also ends the AI process when it is interrupted, not only when it times out.
- Menu bar app: the popover no longer closes when you reach for a menu bar that hides itself (full-screen apps, or "Automatically hide
  and show the menu bar"). Touching the top edge makes macOS give focus back to the app underneath, which closed the popover; it now
  closes on a click anywhere else, when the app loses focus away from the top of a display, or when you switch Space; a click on the
  icon moves a popover left on another Space to the one you are on.
- Menu bar app: put sessions together in a chain from the app. The pencil on a session has an "Add to a chain" line: type any name
  (not only a PR number) or pick a chain that exists, with or without a note. The empty Chains tab now says how to start one.
- Keep what you want: `chainlog keep REF` copies a session (compressed) into chainlog's folder so Claude's cleanup cannot take it.
  Saving or starring a session does this automatically (`keep.auto`). Reopening a session whose original Claude deleted puts the copy
  back first (also from the menu bar app and VS Code). `chainlog unkeep`, `ls --kept`, `restore` and `keep --refresh` round it out.
- `chainlog retention [DAYS]` shows and sets how long Claude keeps transcripts (`cleanupPeriodDays`): it shows the change, warns how
  many sessions a shorter value would delete, asks, and backs the old file up. Any number of days from 1 to 3650.
- `chainlog delete REF` deletes a session from Claude, from chainlog, or both (`--from`). It refuses a running session, asks first,
  and only ever touches `~/.claude/projects/<project>/<session id>.jsonl` and the folder beside it.
- Menu bar app, ⚙ menu: "Claude keeps sessions for N days…" (type any number of days; it shows what shortening would delete and asks), a
  warning line when sessions that have no kept copy are about to expire (no warning when they are all kept), and a switch for
  keeping a copy automatically when you save or star. Tested against sessions aged from half a day to over two years under
  retention settings from 1 to 3650 days (`tests/test_retention_ages.py`).
- Chains, found by testing them with seeded data (`tests/test_chains_deep.py`): rounds that started in the same second no longer swap
  places depending on how the disk lists the files; a round with no start time is placed by when it was last used instead of
  becoming round 1; a chain name with invisible characters (zero-width spaces, bidi marks) is the same chain as the plain name.
  In the menu bar app the Chains search also matches the titles and results of every round, the "All N chains in Terminal…" link
  names the real total while searching, the "only the newest 30 are loaded" hint now appears, and the search box says what it searches.
- Menu bar app: copy what reopens a session, to paste somewhere else. Hover any row (search results too) for a copy button
  (the command: `cd FOLDER && claude --resume ID`, or `codex resume ID`), and for sessions started in VS Code a link button
  (the `vscode://anthropic.claude-code/open?session=ID` link). Right-click also has "Copy session id". `chainlog _menu` rows
  carry `resume.session_id` and `resume.link`. A session whose transcript is gone copies no command (it says why).
- Menu bar app: sessions you keep say "kept"; right-click → Keep a copy, and Delete… (three scopes, with a confirmation). The app
  refreshes kept copies at launch and every 20 minutes.
- The README no longer says chainlog never changes your sessions: these commands do, only when you run them.

- Sessions started in the Claude Code VS Code extension (and the Claude desktop app) are tagged "VS Code" / "Desktop" in the
  menu bar app, `chainlog show` says where a session started, `chainlog ls --via vscode|desktop|terminal` filters by it, and
  searching "vscode" finds them. In the app, right-click → "Open in VS Code" reopens an extension session in the extension.
  They were already imported once each (the extension writes the same transcripts as the CLI); this makes them easy to tell apart.

- Menu bar app: the Sessions tab lists every session (it showed only 16, so most of 182 could only be found by search) and
  its number is the total, not the 11 unsaved from the last 24 h. The Unsaved header now says "last 24 h", and a cut list says
  how many are not shown. `chainlog _menu` returns `counts.all`, `unsaved_window_hours`, and up to 300 other sessions.

- Menu bar app: clicking the icon a second time now closes the popover. A popover that closes when you click elsewhere
  closes on the mouse down, so the icon's own click then reopened it at once. The click that just closed it is ignored.

- Menu bar app, second display: the popover opens under the icon you clicked, on the display you clicked it on (it used
  to be anchored to the menu bar item, which macOS 26 gives no screen, so it landed at a far edge). Hovering a session
  now shows its summary in a panel at once instead of after the system tooltip's 1–2 s delay. `chainlog setup menubar`
  also rebuilds the app when its sources changed, not only when the version number did.

- Fixes from a code review of the menu bar work: one event for a tool chainlog does not know no longer crashes
  `_menu`; the age is the last activity (as in `chainlog ls`); a session in several chains is labelled with the chain
  it is listed under; `_menu` counts all chains (lists 30) and a search answers with just the search, starred-only on
  the Starred tab; the app asks before opening a running session, keeps a half-written note when the popover closes,
  remembers which chains are expanded, opens the newest round on Enter in Chains, and searches for text that starts
  with a dash; `chainlog uninstall` removes the Login Items entry; a damaged `Info.plist` no longer crashes setup.
- Starred sessions: `chainlog star REF…` / `unstar REF…` mark favourites, `chainlog ls --starred` lists them, and a
  ★ shows in every list and in `show`. A star is an event like any other (`chainlog undo` takes it back) and
  keeps a session in your lists even if it has no prompt. `chainlog _menu` now also returns the starred sessions
  and, with `--find TEXT`, a search across all sessions (for the menu bar app).

- Mac menu bar app: an icon at the top of the screen with the unsaved count. Click it for a popover with a search
  box (across all sessions) and three tabs: Sessions (unsaved, recent), Starred and Chains. Click a session to
  reopen it in Terminal (⌥-click copies the command); hover it to star it or to save it with a note and a result.
  `chainlog setup menubar` builds it from `plugin/menubar/*.swift` (needs `swiftc`) and `chainlog uninstall`
  removes it. Behind it, the hidden `chainlog _menu` prints the lists as JSON (`chainlog.menu/1`, see SCHEMA.md).
- Fix: secrets in `NAME=value` form were not redacted when the name had a prefix or suffix
  (`DB_PASSWORD`, `AWS_SECRET_ACCESS_KEY`, `MY_API_TOKEN`, `client_secret`). Also redacted now: quoted JSON keys
  (`"password": "…"`), `--password x`, `sshpass -p`, `mysql -pSECRET`, `--user_pass=`, `Authorization: Basic …`.
  `max_tokens=500` and similar are left alone, and redacting twice changes nothing.
- Fix: a session whose PR link and title name different PRs was filed under the link's PR. chainlog no longer
  guesses: `save` and `review --auto` say which two numbers disagree, and `-c` still lets you choose.
- `chainlog save` with nothing to save (no note, status, tag, title, URL or chain) is refused with an example,
  instead of silently marking the session saved.
- `chainlog review --auto` saves every unsaved session with an automatic summary in one go (asks first; `--yes`).
- A mistyped command (`chainlog svae "note"`) is no longer just searched for: chainlog suggests `chainlog save "note"`.
- `chainlog` shows one tip when Claude Code is installed but not connected (`chainlog setup claude`), in a
  terminal only. `chainlog config set hints off` hides it.
- `save` reloads the index once instead of three times; `doctor` and the tip share one "is it connected" check.

- Open a session straight from the list: `chainlog`, `find`, `ls` and `chain KEY` end with
  "Type a number to open it, or words to search" in a terminal (`CHAINLOG_NO_PROMPT=1` turns it off).

- README rewrite: install paths, a 3-command quick start, chain-testing walkthrough, every command
  explained, settings, FAQ. Screenshots are generated from real output by `docs/screenshots.py`.
- `install.sh` falls back to the latest `main` branch while no release is published.
- `FORCE_COLOR` / `CHAINLOG_COLOR=always` force colours (e.g. when piping).
- Fix: colour codes no longer break column alignment in `chainlog chain`.
- Beginner-friendly README (user journey, simple words), tested with 5 non-technical personas; full
  reference moved to docs/reference.md.
- Setup asks one plain question per step (`d` shows the exact file changes); clearer policy question.
- Uninstall: explicit `y`, prints "Cancelled" on no, keeps the command and notes and says so.
- `config set unsaved.action remind|summarize` offers to turn on the background timer it needs.
- `@N` numbers are remembered per terminal tab / pane; the chain view shows `@N` too.
- Plain wording in save, scan, doctor (verdict line + plain names) and review (`f` = hide).
- Saving onto a session that already has a note asks first (guessed matches and `--id`).
- Fix: previews showed "…" in terminals that report 0 columns.

## 0.1.0 (unreleased)

First version.
- `chainlog save` after `/exit` or from inside a session. Matching goes, in order: process ancestry,
  then terminal, tmux or herdr pane, then folder (saved with an undo hint), then a pick-list.
- Read-only index of existing Claude Code and Codex sessions, with titles, PR links, branches, and
  last-ask / last-work previews.
- PR chains across harnesses: `chain`, `next`.
- Unsaved-session policy: auto-summary (local or the session's own AI), an unsaved list with
  previews, or a reminder. The default wait is 10 minutes.
- `review`, `undo`, `config`, `doctor`, `setup` (diff, consent, backup) and `uninstall`
  (byte-identical restore).
- A Claude Code plugin with hooks and the `/chainlog:save`, `/chainlog:find` and `/chainlog:setup`
  skills. Codex gets user-level hooks plus skills. Optional zsh prompt hint.
