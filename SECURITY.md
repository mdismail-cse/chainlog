# Security

## What chainlog reads
Session transcripts and metadata that Claude Code and Codex already keep on your machine, read-only:
- Claude Code: `~/.claude/projects/*/*.jsonl` and the live registry `~/.claude/sessions/*.json`.
- Codex: `~/.codex/sessions/**`, `~/.codex/archived_sessions/**`, `~/.codex/session_index.jsonl`.

It reads only an allow-list of registry fields and never stores message bodies or tool output.

## What chainlog never opens
`*.key`, `~/.codex/auth.json`, `~/.claude/history.jsonl`, `~/.codex/history.jsonl`, Codex `*.sqlite*`
databases, and `~/.claude/.credentials.json`. This is enforced in code (`paths.safe_open`) and by tests.

## What chainlog writes
- Its own data in `~/.local/share/chainlog` (0700/0600): your notes, statuses, chains, hook metadata,
  and 160-character redacted previews. Set `snippets` to false to drop the previews.
- During `chainlog setup`, and only after showing you a diff and asking:
  - the Claude plugin, installed through `claude plugin install`;
  - one `SessionEnd` entry in `~/.codex/hooks.json`;
  - two skills in `~/.codex/skills/`;
  - one marked block in `~/.zshrc`;
  - a launchd agent or systemd timer, for the reminder and auto-summary policies.

  Every edited file is backed up, and `chainlog uninstall` restores it byte-for-byte.
- chainlog never edits herdr's hook scripts, herdr's hook entries, or `~/.claude/settings.json`.

## Network
None by default. With `summary.engine` set to `auto`, `claude` or `codex`, a short redacted excerpt
goes to the provider that ran the session, through that provider's own CLI. The excerpt contains only
user and assistant text, at most 12k characters. Notifications show only the title and short ID.

## Reporting
Please open a private security advisory on https://github.com/mdismail-cse/chainlog/security/advisories.
