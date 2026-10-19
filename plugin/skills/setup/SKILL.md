---
name: setup
description: Set up chainlog's shell command, Codex integration, zsh prompt hint and the unsaved-session policy, showing every change first.
disable-model-invocation: true
allowed-tools: Bash(chainlog setup *) Bash(chainlog doctor *)
---
1. Run `chainlog setup all --dry-run` and show the user the full output (it lists every file change as a diff).
2. Ask the user two things:
   - Which parts to apply (cli, codex, shell, policy) — default all.
   - "If a session ends unsaved for 10 minutes, should chainlog: 1) auto-save it with a short summary,
     2) keep it in an unsaved list with a preview of the last work, 3) remind you, or 4) do nothing?"
     For 1, also ask: "summary written by chainlog (free, offline) or by the session's own AI (uses a little of your plan)?"
3. Only after the user agrees, run
   `chainlog setup <parts> --yes --policy <summarize|list|remind|off> [--engine local|auto]`
   (add `scheduler` to the parts when the answer was 1 or 3).
4. Run `chainlog doctor` and show the result. Remind the user of the one manual step if Codex was set up:
   open `codex`, type `/hooks`, and trust the chainlog SessionEnd hook.

Never edit ~/.claude/settings.json, herdr's hook scripts, or herdr's hook entries yourself.
