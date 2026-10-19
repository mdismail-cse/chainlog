# chainlog zsh integration — sourced from ~/.zshrc by `chainlog setup shell`.
# On a normal prompt this spawns nothing: it only compares a file mtime and a number using zsh
# builtins (zsh/stat, zsh/datetime). It runs chainlog only when
#   - an agent session just ended (spool/.last changed): one "ended unsaved" line if it ran here
#   - the unsaved-session policy is due (spool/.due reached): list / reminder / auto-summary
zmodload zsh/datetime 2>/dev/null || return 0
zmodload -F zsh/stat b:zstat 2>/dev/null || return 0
typeset -g _chainlog_dir="${CHAINLOG_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/chainlog}/spool"
typeset -g _chainlog_seen=$EPOCHSECONDS
typeset -g _chainlog_try=0

_chainlog_precmd() {
  emulate -L zsh
  local -a m
  local due
  if [[ -f $_chainlog_dir/.last ]] && zstat -A m +mtime -- $_chainlog_dir/.last 2>/dev/null \
      && (( m[1] > _chainlog_seen )); then
    _chainlog_seen=$m[1]
    command chainlog _nudge --shell-pid $$ 2>/dev/null
  fi
  if [[ -r $_chainlog_dir/.due ]] && (( EPOCHSECONDS - _chainlog_try >= 60 )); then
    read -r due < $_chainlog_dir/.due 2>/dev/null
    if [[ $due == <-> ]] && (( EPOCHSECONDS >= due )); then
      _chainlog_try=$EPOCHSECONDS
      command chainlog _sweep --tty --shell-pid $$ 2>/dev/null
    fi
  fi
}
autoload -Uz add-zsh-hook && add-zsh-hook precmd _chainlog_precmd
