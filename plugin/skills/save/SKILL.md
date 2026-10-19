---
name: save
description: Bookmark this session in chainlog with a short QA note, so it can be found and resumed later.
argument-hint: "[note] [-s pass|fail|blocked|wip] [-c repo#pr]"
disable-model-invocation: true
allowed-tools: Bash(chainlog save *)
---
Save this session's bookmark with the user's note: `$ARGUMENTS`

1. Choose a heredoc delimiter for this call: `CHAINLOG_` followed by 8 random capital letters and
   digits you pick now (for example `CHAINLOG_K7Q2ZP9M`). If that exact text occurs anywhere in the
   note, pick a different one. Never reuse the example.
2. Run ONE Bash call: the command below with your delimiter in both places. Put the note between the
   two delimiter lines exactly as the user wrote it: do not quote, escape, reword or shorten it.
   The opening delimiter stays in single quotes, so nothing in the note is run by the shell.

```
chainlog save --via skill:claude --args-stdin <<'<DELIMITER>'
<the note, exactly as written above>
<DELIMITER>
```

3. Reply with the single line it prints.

If it exits with code 2, it could not tell which session is meant: it printed JSON with a
`candidates` list. Show them as a short numbered list (short id, title, last work), ask the user which
one, then run the same command again with `--id <short>` added after `--args-stdin`.
