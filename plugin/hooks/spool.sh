#!/bin/sh
# chainlog spool v1 — spool.sh <claude|codex> <start|end>. Silent, always exit 0.
# Captures the hook payload and the terminal fingerprint; never parses, locks or starts Python.
# Contract: SCHEMA.md "Spool v1". Kept to ~7 forks so it stays well inside the exit-hook budget.
umask 077; exec 2>/dev/null
[ -n "${CHAINLOG_SUMMARIZER:-}" ] && exit 0   # our own summarizer run: never spool it
d="${CHAINLOG_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/chainlog}/spool"
[ -d "$d" ] || mkdir -p "$d" || exit 0
t=$(date +%s)
b="$t-$$"   # a pid is unique among live processes, so epoch+pid cannot collide
head -c 65536 >"$d/$b.in"
# one ps call: up to 4 ancestor pids starting at $PPID, then the tty of $PPID
pt=$(ps -A -o pid= -o ppid= -o tty= | awk -v p="$PPID" '{pp[$1]=$2; tt[$1]=$3} END{q=p;n=0;while(q>1&&n<4){printf "%s%s",(n?",":""),q;q=pp[q];n++}; printf " %s",tt[p]}')
anc=${pt%% *}; tty=${pt#* }
# sanitise every value in one pass: fields are joined with \036 (ASCII RS; tr keeps it and drops
# newlines and everything else), then split with IFS; the x prefix keeps empty values as fields
v=$(printf 'x%s\036' "$1" "$2" "$tty" "${HERDR_PANE_ID:-}" "${HERDR_WORKSPACE_ID:-}" "${HERDR_TAB_ID:-}" \
  "${TMUX_PANE:-}" "${TERM_SESSION_ID:-}" "${ITERM_SESSION_ID:-}" "${CHAINLOG_CHAIN:-}" \
  | tr -cd 'A-Za-z0-9:_./#\036-' | cut -c1-1400)
[ -n "${ZSH_VERSION:-}" ] && setopt SH_WORD_SPLIT
set -f; IFS=$(printf '\036'); set -- $v; unset IFS
h=${1#x}; e=${2#x}; y=${3#x}; hp=${4#x}; hw=${5#x}; ht=${6#x}; tm=${7#x}; te=${8#x}; it=${9#x}; ch=${10#x}
case "$y" in *[!A-Za-z0-9/]*|'') y= ;; esac   # '??' / '-' mean no tty
printf '{"v":1,"h":"%s","e":"%s","t":%s,"anc":[%s],"tty":"%s","fp":{"herdr_pane":"%s","herdr_ws":"%s","herdr_tab":"%s","tmux":"%s","term":"%s","iterm":"%s"},"chain":"%s"}\n' \
  "$h" "$e" "$t" "$anc" "$y" "$hp" "$hw" "$ht" "$tm" "$te" "$it" "$ch" >"$d/.$b.json" && mv "$d/.$b.json" "$d/$b.json"
if [ "$e" = end ]; then
  : >"$d/.last"
  # unsaved-policy due time: end + $CHAINLOG_HOME/after seconds, only if earlier than a pending one
  a=600; [ -r "$d/../after" ] && read -r a <"$d/../after"
  case "$a" in ''|*[!0-9]*) a=600 ;; esac
  n=$((t + a)); o=
  [ -r "$d/.due" ] && read -r o <"$d/.due"
  case "$o" in ''|*[!0-9]*) o= ;; esac
  if [ -z "$o" ] || [ "$n" -lt "$o" ]; then echo "$n" >"$d/.due"; fi
fi
exit 0
