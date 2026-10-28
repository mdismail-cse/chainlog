# chainlog data formats

All files live in `CHAINLOG_HOME` (default `${XDG_DATA_HOME:-~/.local/share}/chainlog`), mode 0700/0600.
`events.jsonl` is the only file that cannot be rebuilt.

## Spool v1 (hook → core contract)

`spool.sh <claude|codex> <start|end>` runs as the harness hook. It is silent, always exits 0, and
never parses, locks or starts Python. Each firing writes two files in `spool/` with the basename
`b = <epoch>-<pid>`:

1. `b.in`: raw hook stdin, at most 64 KB; it may be invalid JSON.
2. `b.json`: written last (tmp + rename). Its presence is the commit marker. It is built by sh from
   sanitised values (`[A-Za-z0-9:_./#-]`, ≤128 chars each):
   ```json
   {"v":1,"h":"claude","e":"end","t":1760900000,"anc":[97539,49909,412],"tty":"ttys001",
    "fp":{"herdr_pane":"w5:p1","herdr_ws":"w5","herdr_tab":"w5:t1","tmux":"","term":"…","iterm":""},"chain":""}
   ```
   - `anc` is up to 4 ancestor PIDs starting at the hook's parent.
   - `chain` comes from `CHAINLOG_CHAIN`.

An `end` also touches `spool/.last` and creates `spool/.due` (end time + `$CHAINLOG_HOME/after` seconds),
unless a due time is already pending. With `CHAINLOG_SUMMARIZER=1` set, the stub exits immediately.

At ingest, only `session_id, transcript_path, cwd, hook_event_name, reason, source, session_title, model`
are kept from `b.in`. When `b.in` is unparseable, `session_id` is recovered by regex so the fingerprint is
still committed. Unusable files go to `spool/quarantine/` and are deleted after 7 days.

When the data dir is not writable (for example inside a Codex sandbox), the CLI writes complete events
to `${TMPDIR}/chainlog-spool-<uid>/<…>.event.json`. The next writer ingests them.

## Event v1 (`events.jsonl`)

```json
{"v":1,"id":"<hex20>","ts":1760900000123,"type":"save","ref":"claude:<uuid>","src":"cli","data":{}}
```

| type | data |
|---|---|
| `hook.start` / `hook.end` | `reason, source, session_title, transcript_path, cwd, model, anc, tty, fp, chain, spool` |
| `save` | `note?, status?, tags?, chain?, chain_source?, title?, url?`. With `src:"auto"`: `summary, summary_engine, fallback_reason?` |
| `note` | `text` |
| `status` | `value: wip\|pass\|fail\|blocked` |
| `tag` | `add[], remove[]` |
| `chain.link` / `chain.unlink` | `chain, pos?` |
| `star` / `unstar` | — (sets `starred` on the session; a starred session is listed even without a prompt) |
| `keep` / `unkeep` | `bytes?, auto?` (sets `kept`: chainlog holds a copy of the transcript; the copy itself is `kept/claude/<id>.jsonl.gz` + `<id>.json` in the data folder). `forget` may carry `deleted: true` when `chainlog delete` wrote it |
| `forget` / `unforget` | — |
| `unsaved.handled` | `action, result, due_at`, so the sweep acts once per session |
| `undo` | `event`: the id of the event being reverted |

- Hook event ids are `sha256(spool basename)[:20]`, so re-ingest is a no-op. CLI ids are random.
- Lines are at most 4 KB and written with one `write()` under `flock(lock)`, then fsync.
- Changes are additive only. Unknown types and fields are ignored, and the log is never rewritten.

## Fold

`fold(facts, events, live) → sessions, chains` is pure and runs on every call. Its rules:
- Events are sorted by `(ts, position in the log)`, so same-millisecond events keep their order.
- Scalars are last-writer-wins.
- Notes append, and tags are a set.
- Chain links replay in order.
- `undo` cancels the named event.
- Chain rounds are ordered by session start time unless `pos` is set.

## Session record (`--json`, schema `chainlog.session/1`)

Key fields:
- `ref`, `harness`, `native_id`, `short`
- `title {value, source}`: the source is one of user > custom > agent > ai > thread > hook > herdr > first-ask > cwd
- `saved` (`user|auto|none`), `notes[]`, `status`, `tags[]`, `chains[] {id, round, source}`
- `starred` (a favourite: `star` / `unstar` events; a starred session is listed even when it has no prompt)
- `cwd`, `git {branch, remote}`, `prs[]`, `urls[]`
- `times {started_at, last_activity_at, ended_at}`, `end {reason, via}`, `origin`, `lineage`
- `transcript {path, present, archived}`, `last_ask`, `last_work` (redacted, ≤160 characters)
- `stats`, `summary`, `unsaved_handled`, `live`
- `resume {cwd, argv, command}`

New fields may be added within `/1`.

## Menu data (`chainlog _menu`, schema `chainlog.menu/1`)

One line of JSON for the Mac menu bar app (hidden command; not part of the public CLI). Top level:
`schema`, `chainlog` (version), `generated_at`, `unsaved_window_hours`, `keep_auto`, `ask {engine, connected}` (the Ask tab: the engine you chose, and whether you agreed to send it session text), `retention {days, is_default, expiring, expiring_unkept, expiring_within}`
(how long Claude keeps transcripts, and how many expire within `expiring_within` days / have no kept copy), `counts {unsaved, starred, chains, all}`, `unsaved[]`, `recent[]`,
`starred[]`, `chains[]`. `chainlog _menu --find TEXT` adds `query` and `results[]`: every matching session
(not only the listed ones), best match first, at most 40.

- A row: `ref`, `short`, `harness`, `kept` (a copy is held), `via` (where it was started: `"VS Code"`, `"Desktop"`, or null for a plain terminal),
  `title` (one clean line), `saved` (`user|auto|none`), `status`, `live`, `starred`, `chain`, `round`, `at`, `ago`,
  `last_work`, `resume {cwd, command, vscode, session_id, link, restores, blockers[], notes[]}`. `resume.restores` is true when Claude deleted the
  original but a kept copy exists: `command` then starts with `chainlog restore REF && …`, and `blockers` is empty.
  `resume.command` is ready to run in a shell; a non-empty `blockers` means the session cannot be reopened.
  `resume.vscode` is a shell command that reopens a Claude session started in the VS Code extension inside it (macOS
  only, otherwise null): it opens the folder, then the extension's `vscode://anthropic.claude-code/open?session=ID` link.
  `resume.session_id` is the tool's own id for the session (what `claude --resume` takes); `resume.link` is that
  `vscode://` link itself, for pasting somewhere else (only a Claude session started in VS Code whose transcript is on disk,
  otherwise null; unlike `vscode` it is not macOS-only). Both are absent from an older chainlog, and the app copes.
- `counts.unsaved` is the total; `unsaved[]` holds at most the 8 newest, `recent[]` the next 300 (`counts.all` is every
  session), `chains[]` 30;
  `starred[]` holds all starred sessions (at most 50), newest activity first.
- A chain: `id`, `rounds`, `status`, `title`, `ago`, `members[]` (rows, with `round`).

New fields may be added within `/1`; `plugin/menubar/sample.json` shows the shape and is checked by the tests.

## Ask (`chainlog ask`, `chainlog _ask`)

`chainlog ask --status --json` prints `{engine, consented[], model, ready, engines{claude|codex: {installed, signed_in, detail}},
disclosure{claude, codex}}`. `signed_in` is `true`, `false`, or `null` when the tool could not say. `model` is what would be used.
`disclosure` is the text shown before connecting: one source for the terminal and the app.

`chainlog _ask` (hidden; the menu bar app) reads one JSON object on stdin, `{"question": "…", "history": [{"role": "user"|"assistant",
"text": "…"}], "engine": "claude"|"codex" (optional)}`, and prints one compact JSON object per line:

- `{"type":"status","text":"Asking Claude…"}`: zero or more, while it works;
- then exactly one of
  `{"type":"answer","answer":"… [[id]] …","sessions":[row…],"engine":"claude","model":"sonnet","calls":2,"listed":47}`
  (`sessions` are rows as in `_menu`, for the ids named in the answer, at most 12) or
  `{"type":"error","code":"not_connected|no_consent|not_installed|not_signed_in|timeout|empty|failed|bad_request","message":"…"}`.

It exits 0 for both. SIGTERM stops it and the AI process it started (exit 130).

## Sources (read-only)

| Harness | Files | Never listed / never opened |
|---|---|---|
| Claude Code | `~/.claude/projects/*/<id>.jsonl` (top level); registry `~/.claude/sessions/*.json` | `<project>/<id>/subagents/`; `*.key`; `history.jsonl` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl`, `archived_sessions/**`, `session_index.jsonl` | rollouts with `parent_thread_id`; `*.sqlite*`; `auth.json` |
