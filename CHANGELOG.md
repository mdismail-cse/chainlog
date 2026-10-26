# Changelog

## Unreleased

- Fix: secrets in `NAME=value` form were not redacted when the name had a prefix or suffix
  (`DB_PASSWORD`, `AWS_SECRET_ACCESS_KEY`, `MY_API_TOKEN`, `client_secret`). Also redacted now: quoted JSON keys
  (`"password": "…"`), `--password x`, `sshpass -p`, `mysql -pSECRET`, `--user_pass=`, `Authorization: Basic …`.
  `max_tokens=500` and similar are left alone, and redacting twice changes nothing.
- Fix: a session whose PR link and title name different PRs was filed under the link's PR. chainlog no longer
  guesses: `save` and `review --auto` say which two numbers disagree, and `-c` still lets you choose.
- `chainlog save` with nothing to save (no note, status, tag, title, URL or chain) is refused with an example,
  instead of silently marking the session saved.
- `chainlog review --auto` saves every unsaved session with an automatic summary in one go (asks first; `--yes`).
- A mistyped command (`chainlog svae "note"`) is no longer just searched for: chainlog suggests `chainlog save "note"`.
- `chainlog` shows one tip when Claude Code is installed but not connected (`chainlog setup claude`), in a
  terminal only. `chainlog config set hints off` hides it.
- `save` reloads the index once instead of three times; `doctor` and the tip share one "is it connected" check.

- Open a session straight from the list: `chainlog`, `find`, `ls` and `chain KEY` end with
  "Type a number to open it, or words to search" in a terminal (`CHAINLOG_NO_PROMPT=1` turns it off).

- README rewrite: install paths, a 3-command quick start, chain-testing walkthrough, every command
  explained, settings, FAQ. Screenshots are generated from real output by `docs/screenshots.py`.
- `install.sh` falls back to the latest `main` branch while no release is published.
- `FORCE_COLOR` / `CHAINLOG_COLOR=always` force colours (e.g. when piping).
- Fix: colour codes no longer break column alignment in `chainlog chain`.
- Beginner-friendly README (user journey, simple words), tested with 5 non-technical personas; full
  reference moved to docs/reference.md.
- Setup asks one plain question per step (`d` shows the exact file changes); clearer policy question.
- Uninstall: explicit `y`, prints "Cancelled" on no, keeps the command and notes and says so.
- `config set unsaved.action remind|summarize` offers to turn on the background timer it needs.
- `@N` numbers are remembered per terminal tab / pane; the chain view shows `@N` too.
- Plain wording in save, scan, doctor (verdict line + plain names) and review (`f` = hide).
- Saving onto a session that already has a note asks first (guessed matches and `--id`).
- Fix: previews showed "…" in terminals that report 0 columns.

## 0.1.0 (unreleased)

First version.
- `chainlog save` after `/exit` or from inside a session. Matching goes, in order: process ancestry,
  then terminal, tmux or herdr pane, then folder (saved with an undo hint), then a pick-list.
- Read-only index of existing Claude Code and Codex sessions, with titles, PR links, branches, and
  last-ask / last-work previews.
- PR chains across harnesses: `chain`, `next`.
- Unsaved-session policy: auto-summary (local or the session's own AI), an unsaved list with
  previews, or a reminder. The default wait is 10 minutes.
- `review`, `undo`, `config`, `doctor`, `setup` (diff, consent, backup) and `uninstall`
  (byte-identical restore).
- A Claude Code plugin with hooks and the `/chainlog:save`, `/chainlog:find` and `/chainlog:setup`
  skills. Codex gets user-level hooks plus skills. Optional zsh prompt hint.
