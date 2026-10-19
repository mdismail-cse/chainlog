# Installing chainlog (for AI agents)

Goal: the user can run `chainlog save "note"` right after exiting a Claude Code or Codex session,
find it later with `chainlog find …`, and resume it with `chainlog resume …` / `chainlog next …`.

Follow these steps in order. Show the user every diff and get their consent before step 5.

1. **Preflight.** Report versions; skip any harness that is missing.
   ```sh
   python3 --version        # need >= 3.9
   claude --version; codex --version; herdr --version
   ```
2. **Install the CLI** (puts files only under `~/.local/share/chainlog` and `~/.local/bin`):
   ```sh
   curl -fsSL https://raw.githubusercontent.com/mdismail-cse/chainlog/main/install.sh | sh -s -- --no-setup
   ```
3. **Show the plan:** `chainlog setup all --dry-run` (add `--policy <choice>` once you know it, so the
   plan also lists the background sweep for summarize/remind). Paste the full output to the user. It lists
   each change: the Claude plugin install commands, one `SessionEnd` entry in `~/.codex/hooks.json`
   (as a diff), two Codex skills, and one marked block in `~/.zshrc`.
4. **Ask the user**, in these words or close to them:
   - "Which parts should I set up: the Claude Code plugin, the Codex hook and skills, the zsh
     prompt hint? (default: all)"
   - "If a session ends and isn't saved within 10 minutes, should chainlog: 1) auto-save it with a
     short summary, 2) keep it in an unsaved list with a preview of the last work, 3) remind you,
     or 4) do nothing?"
   - If 1: "Should the summary be written by chainlog itself (free, offline) or by the session's
     own AI (better, uses a little of your plan)?"
5. **Apply**, only after consent. Add one `--skip` per declined part (`claude`, `codex`, `shell`):
   ```sh
   chainlog setup all --yes --policy <summarize|list|remind|off> [--engine local|auto] [--skip claude] [--skip codex] [--skip shell]
   ```
   - For `summarize` and `remind`, this also installs the 2-minute background sweep (a launchd agent
     on macOS, a systemd user timer on Linux). The dry run in step 3 lists it when you pass the same
     `--policy`. Add `--skip scheduler` if the user declined it.
   - Exit codes: 0 means done; 1 means a step FAILED (the output says which; finished steps are
     kept); 2 means nothing was applied, because `--yes` was missing or a part name is unknown.
   - With `--json`, stdout is a single JSON document. Progress lines go to stderr.
6. **Verify:** `chainlog doctor --json`.
   - Expect `ok: true` for everything the user accepted.
   - Checks for parts they declined (`claude_plugin`, `codex_*`, `shell_hint`) and
     `codex_hooks_trusted` may stay `ok: false` with severity `warn`/`info`; that is expected.
   - `claude_retention` warns while Claude keeps transcripts for fewer than 60 days. Tell the user
     (sessions older than that cannot be resumed); do not edit `~/.claude/settings.json` yourself.
   - `cli_on_path` can warn inside an agent's shell when `~/.local/bin` is not on its PATH; the
     user's own terminal gets it from the zsh block (or add `export PATH="$HOME/.local/bin:$PATH"`).
7. **Only if Codex was set up**, tell the user the one manual step: open `codex`, type `/hooks`,
   and trust the chainlog `SessionEnd` hook.

## Do not
- edit `~/.claude/hooks/herdr-agent-state.sh`, `~/.codex/herdr-agent-state.sh`, or herdr's hook entries
- reorder anything in `~/.codex/hooks.json`, or edit `~/.claude/settings.json`
- read `~/.codex/auth.json`, `~/.claude/sessions/*.key`, `~/.claude/history.jsonl` or any token
- start claude or codex sessions

## Undo
`chainlog uninstall` removes the integrations and keeps the user's notes.
`chainlog uninstall --purge` also deletes all chainlog data.
