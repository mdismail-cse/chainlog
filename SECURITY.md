# Security

## What chainlog reads
Session transcripts and metadata that Claude Code and Codex already keep on your machine, read-only:
- Claude Code: `~/.claude/projects/*/*.jsonl` and the live registry `~/.claude/sessions/*.json`.
- Codex: `~/.codex/sessions/**`, `~/.codex/archived_sessions/**`, `~/.codex/session_index.jsonl`.

It reads only an allow-list of registry fields and does not store message bodies or tool output, with one exception you control:
the copies of sessions you keep (below).

## What chainlog never opens
`*.key`, `~/.codex/auth.json`, `~/.claude/history.jsonl`, `~/.codex/history.jsonl`, Codex `*.sqlite*`
databases, and `~/.claude/.credentials.json`. This is enforced in code (`paths.safe_open`) and by tests.

## What chainlog writes
- Its own data in `~/.local/share/chainlog` (0700/0600): your notes, statuses, chains, hook metadata,
  and 160-character redacted previews. Set `snippets` to false to drop the previews. `config.json` also records which AI tool
  you agreed to send session text to (`ask.consent`); the chat itself is kept only in the menu bar app's memory.
- A compressed **copy of the whole transcript** of each session you keep, in `~/.local/share/chainlog/kept/` (0700/0600). You keep a
  session with `chainlog keep`, or automatically by saving or starring it (setting `keep.auto`, on by default; turn it off with
  `chainlog config set keep.auto false`). The copy is **not redacted**: it holds everything the original holds, including anything you
  pasted into the conversation, such as a password or key, so that text then exists in two places. `chainlog unkeep` deletes a copy;
  `chainlog uninstall --purge` deletes all of chainlog's data.
- In Claude's own folders, only when you run the command, and only these:
  - `chainlog delete`: the session's transcript (`~/.claude/projects/<project>/<session id>.jsonl`) and the `<session id>/` folder
    beside it. It asks first, refuses a running session, and refuses any other path;
  - `chainlog retention DAYS`: the single `cleanupPeriodDays` setting in `~/.claude/settings.json`, after showing the change, with
    the old file backed up in chainlog's data folder;
  - reopening a session whose original Claude deleted: the kept copy is written back to where it was.
- During `chainlog setup`, and only after showing you a diff and asking:
  - the Claude plugin, installed through `claude plugin install`;
  - one `SessionEnd` entry in `~/.codex/hooks.json`;
  - two skills in `~/.codex/skills/`;
  - one marked block in `~/.zshrc`;
  - a launchd agent or systemd timer, for the reminder and auto-summary policies.

  Every edited file is backed up, and `chainlog uninstall` restores it byte-for-byte.
- chainlog never edits herdr's hook scripts, herdr's hook entries, or `~/.claude/settings.json`.

## Network
None by default. Two features send text to an AI provider, through that provider's own signed-in CLI (`claude -p`, `codex exec`),
and only when you turn them on:
- **Summaries.** With `summary.engine` set to `auto`, `claude` or `codex`, a short redacted excerpt goes to the provider that ran the
  session. The excerpt contains only user and assistant text, at most 12k characters.
- **Ask** (`chainlog ask`, the menu bar app's Ask tab). After you agree (`chainlog ask --connect`, which shows exactly what is sent), each
  question sends the question, the last messages of the chat, and a redacted text catalog of your sessions (titles, notes, short
  previews, results; plus, when the AI asks for them, a few sessions' in-depth text) to the tool you chose, which can be a different
  provider from the one that ran a session. `chainlog ask --disconnect` withdraws the agreement.

Both run the AI with **no tools** (no files, shell, connectors, plugins, hooks or your own config), from an empty temporary folder,
and leave no session behind. Notifications show only the title and short ID.

## Reporting
Please open a private security advisory on https://github.com/mdismail-cse/chainlog/security/advisories.
