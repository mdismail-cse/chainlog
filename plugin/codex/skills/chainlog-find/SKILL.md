---
name: chainlog-find
description: Use when the user asks for an earlier Claude Code or Codex session — by PR number, test chain, note, title or branch — or wants the latest round of a chain.
---
Run `chainlog find '<words>' --json -n 8` — the words in ONE single-quoted argument (an unquoted `#4321` would start a shell comment) with the distinctive words of the request (PR numbers,
feature, folder or branch names; filler words are ignored). Results are ranked by how many match.

Summarise the best matches as a short numbered list (harness, `short` id, title, latest note or
summary, chain and round, status, how long ago), give the exact `resume.command` of the best match
and any `resume.warnings`. `chainlog chain <key>` shows every round of a chain in order.

Do not run the resume command yourself; the user resumes from their own terminal.
