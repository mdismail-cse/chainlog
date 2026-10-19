# Changelog

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
