---
name: chainlog-save
description: Use when the user asks to save, bookmark or note this Codex session in chainlog (QA chain testing), e.g. "save this to chainlog: r3 blocked on staging DB".
---
1. Take the user's note text exactly as they wrote it. If they clearly state a result word
   (pass, fail, blocked, wip) as the session status, add ` -s <word>` at the very end; if they name
   a PR chain, add ` -c <repo#number>`. Do not change anything else.
2. Choose a heredoc delimiter for this call: `CHAINLOG_` plus 8 random capital letters and digits
   you pick now. If that text occurs anywhere in the note, pick another. Never reuse an example.
3. Run ONE shell command, with your delimiter in both places and the note between the lines
   unquoted and unescaped (the quoted delimiter keeps it plain data):

```
chainlog save --via skill:codex --args-stdin <<'<DELIMITER>'
<the note>
<DELIMITER>
```

4. Reply with the single line it prints.

If it exits with code 2, it could not tell which session is meant and printed JSON `candidates`:
show them as a numbered list (short id, title, last work), ask the user, then run the same command
again with `--id <short>` added after `--args-stdin`.

If the sandbox blocks the command, ask the user to approve it (suggest "always allow" for
`chainlog save`).
