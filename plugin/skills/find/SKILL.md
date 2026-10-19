---
name: find
description: Find a past Claude Code or Codex session in chainlog (by PR number, chain, note, title, branch or folder) and show how to resume it. Use when the user asks for an earlier session, "the session where I tested PR 4321", or the latest round of a test chain.
argument-hint: "[search words, e.g. 4321 refund]"
allowed-tools: Bash(chainlog find *) Bash(chainlog show *) Bash(chainlog chain *)
---
Run `chainlog find '<words>' --json -n 8` — the words in ONE single-quoted argument (an unquoted `#4321` would start a shell comment), where <words> are the distinctive words of the request:
PR numbers, feature or page names, folder or branch names (filler like "the session where I" is
ignored anyway). Results are ranked by how many words match.

From the JSON, summarise the best matches as a short numbered list: harness, `short` id, title,
latest note or summary, chain and round, status, and how long ago. Then give the exact
`resume.command` of the best match, and mention any `resume.warnings`. For a chain,
`chainlog chain <key>` shows every round in order.

Do not run the resume command yourself — the user resumes from their own terminal.
