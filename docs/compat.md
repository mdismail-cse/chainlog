# Compatibility evidence (Day-0 spike)

Machine: macOS (Darwin 25.5), Claude Code 2.1.303, Codex CLI 0.161.0, herdr 0.9.0,
`/usr/bin/python3` 3.9.6. Date: 2026-10-19. **V** = verified here, **U** = still unverified.

| # | Question | Result | Evidence |
|---|---|---|---|
| S1 | Do Claude plugin hooks fire? | **V** (print mode) | `claude -p --no-session-persistence --plugin-dir ./plugin`: SessionStart stdin `{session_id, transcript_path, cwd, hook_event_name, source:"startup"}` and SessionEnd stdin `{…, reason:"other", prompt_id}`. Both spooled, with ancestry. The string-form `command` with `${CLAUDE_PLUGIN_ROOT}` works |
| S1b | SessionEnd on interactive `/exit`, Ctrl-D, herdr pane close, terminal close | **U** | Needs an interactive run. Backfill and reconcile cover a missed end |
| S2 | `$ARGUMENTS` inside a ```` ```! ```` block | **U** | The skill uses the model-run quoted heredoc (safe baseline) |
| S3 | Bash-mode `!` inherits `CLAUDE_CODE_SESSION_ID` | **U** | Set in the Bash tool [V]. Fallback: registry via ancestry |
| S4 | `CODEX_THREAD_ID` in Codex shell commands | **U** | herdr's own hook reads it. Fallback: herdr pane, fingerprint, folder |
| S5 | User-level Codex `SessionEnd` fires after `/hooks` trust | **U** | `codex features list` shows `hooks stable true` [V] |
| S6 | `claude --resume <id>` from a foreign cwd | **U** | chainlog always `cd`s to the session's folder first |
| S7 | Does `herdr integration install codex` overwrite `~/.codex/hooks.json`? | **U** | The `codex_hook_present` doctor check detects a missing entry |
| S8 | Codex discovers `~/.codex/skills/<name>/SKILL.md` | **U** | The directory exists (empty) [V] |
| S10 | Codex sandbox blocks writes to `~/.local/share` | **U** | TMPDIR fallback spool is implemented and tested |
| S11 | `herdr pane current` after the agent exits | **U** | Shape [V]: `result.pane.{pane_id, agent, agent_session.value, agent_status, terminal_title_stripped}` |
| S13 | Claude summarizer leaves no trace | **V** | `claude -p --no-session-persistence --setting-sources project --model haiku` in an empty temp dir: 3.5 s, transcript count unchanged (141), no spool files. `--bare` was rejected: its help says it is API-key-only |
| S14 | Codex summarizer leaves no trace | **V** | `codex exec --ephemeral --skip-git-repo-check -s read-only -C <tmp> -o <file> -`: 7.5 s, rollout count unchanged (37) |
| S15 | Notifications from launchd (osascript / herdr) | **U** | Argv-form `osascript` (no injection) is unit-tested |
| S16 | launchd agent finds `claude`/`codex`/`herdr` | **U** | Setup writes the current `PATH` into the plist |

## Other findings during the build
- **Registry start time.** `~/.claude/sessions/<pid>.json` `procStart` is UTC, while `ps -o lstart` prints local time. The PID-reuse check compares them modulo whole time-zone offsets. Without this, every live session was rejected.
- **Codex prompts.** User prompts are `response_item` messages with `role: user` and `input_text`, mixed with injected context (`<environment_context>`, `# AGENTS.md …`). `event_msg.user_message` is rare (16 lines across all rollouts).
- **Child sessions.** 25 of the 37 Codex rollouts are child agents (`parent_thread_id`, `source.subagent`). They are excluded, leaving 12 top-level sessions.
- **Hook stub latency.** p50 53 ms, p95 63 ms (n=200). One `ps -A` call is about 19 ms of that. The budget is Claude's 1.5 s SessionEnd limit.
- **Index time.** Cold index of 141 Claude + 37 Codex files: 2.2 s wall-clock. Warm: 0.15 s.
- **Codex `hooks.json`.** herdr writes it without a trailing newline. Setup keeps that style, so the diff shows only our entry.
