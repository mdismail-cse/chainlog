#!/bin/sh
# Build, install or remove the chainlog menu bar app (macOS only; needs the Xcode command line tools).
#   sh build.sh              build into a temporary folder and say where it is
#   sh build.sh --install    build, copy to ~/Applications/Chainlog.app (or $CHAINLOG_APP_DIR) and open it
#   sh build.sh --uninstall  quit the app and delete it
#   sh build.sh --check      build, then print the popover from sample.json and check it (no window)
#   sh build.sh --shots DIR  build, then draw the popover from sample.json into DIR as PNGs (light and dark)
set -eu

here=$(cd "$(dirname "$0")" && pwd)
dir="${CHAINLOG_APP_DIR:-$HOME/Applications}"
app="$dir/Chainlog.app"
version=$(cat "$here/../VERSION")
# a fingerprint of the Swift sources, stamped into the app so `chainlog setup menubar` can tell a stale build
# (the version number alone does not change between fixes). Same recipe as components.menubar_source_hash().
src=$(for f in $(ls "$here"/*.swift | LC_ALL=C sort); do cat "$f"; done | shasum -a 256 | cut -c1-16)

die() { printf 'chainlog menubar: %s\n' "$*" >&2; exit 1; }

quit_running() {
  pkill -x Chainlog 2>/dev/null || true
  sleep 0.3
}

build() {  # build <output dir>  -> <output dir>/Chainlog.app
  [ "$(uname)" = Darwin ] || die "the menu bar app is for macOS."
  command -v swiftc >/dev/null 2>&1 || die "swiftc not found. Install the command line tools: xcode-select --install"
  out="$1/Chainlog.app"
  rm -rf "$out"
  mkdir -p "$out/Contents/MacOS"
  sed "s/__VERSION__/$version/g; s/__SRC__/$src/g" "$here/Info.plist" > "$out/Contents/Info.plist"
  swiftc -Onone -swift-version 5 -parse-as-library -o "$out/Contents/MacOS/Chainlog" "$here"/*.swift \
    -framework AppKit -framework SwiftUI -framework Combine -framework ServiceManagement 2>&1 | grep -v "^$" || true
  [ -x "$out/Contents/MacOS/Chainlog" ] || die "the build failed (see the messages above)."
  codesign --force --sign - "$out" >/dev/null 2>&1 || true   # ad-hoc: lets macOS run it and keep its login item
}

case "${1:-build}" in
  --install)
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/chainlog-menubar.XXXXXX")
    stage=
    trap 'rm -rf "$tmp"; [ -z "$stage" ] || rm -rf "$stage"' EXIT INT TERM
    build "$tmp"
    # copy next to the final place first: if that fails (disk full, no permission) the app you have stays untouched
    mkdir -p "$dir" || die "cannot create $dir"
    stage="$dir/.Chainlog.app.new"
    rm -rf "$stage"
    cp -R "$tmp/Chainlog.app" "$stage" || die "cannot write into $dir (the installed app was left as it was)"
    quit_running
    rm -rf "$app"
    mv "$stage" "$app"
    stage=
    open "$app"
    printf 'installed %s\n' "$app"
    ;;
  --uninstall)
    # take it out of System Settings > Login Items first, while there is still an app to ask
    [ ! -x "$app/Contents/MacOS/Chainlog" ] || "$app/Contents/MacOS/Chainlog" --unregister >/dev/null 2>&1 || true
    quit_running
    rm -rf "$app"
    printf 'removed %s\n' "$app"
    ;;
  --check)
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/chainlog-menubar.XXXXXX")
    trap 'rm -rf "$tmp"' EXIT INT TERM
    build "$tmp"
    bin="$tmp/Chainlog.app/Contents/MacOS/Chainlog"
    dump() { "$bin" --dump "$here/sample.json" "$@"; }
    found="$here/sample-find.json"
    want() {  # want TEXT WHAT-IS-WRONG: succeeds if stdin contains TEXT (use as: ... | want ... || exit 1)
      grep -qF -- "$1" && return 0
      printf 'chainlog menubar: check failed: %s (looking for "%s")\n' "$2" "$1" >&2
      return 1
    }
    out=$(dump) || die "check failed: --dump exited with an error"
    printf '%s\n' "$out"
    printf '%s\n' "$out" | want "Unsaved · 3" "the Sessions tab has no Unsaved section" || exit 1
    printf '%s\n' "$out" | want "Everything else · 4" "the Sessions tab does not list every other session" || exit 1
    printf '%s\n' "$out" | want "Unsaved · 3 · last 24 h" "the Unsaved header does not say what unsaved covers" || exit 1
    printf '%s\n' "$out" | want "Sessions 7*" "the Sessions tab does not count every session" || exit 1
    printf '%s\n' "$out" | want "(can't reopen)" "a session with a deleted transcript is not marked" || exit 1
    printf '%s\n' "$out" | want "shop#4321 r2 · kept · 1d" "a session with a kept copy has no tag" || exit 1
    printf '%s\n' "$out" | want "Shop PR 4402 invoice emails — VS Code · 20m" "a session started in VS Code has no tag" || exit 1
    printf '%s\n' "$out" | want "Shop PR 4388 coupon stacking — Desktop · 2h" "a session started in the desktop app has no tag" || exit 1
    printf '%s\n' "$out" | want "★ Tracker CSV export" "a starred session has no star" || exit 1
    dump --tab starred | want "Starred · 2" "the Starred tab does not list the 2 starred sessions" || exit 1
    dump --tab chains | want "Continue the latest round" "the Chains tab has no continue line" || exit 1
    dump --query refund --find-file "$found" | want "Results · 7" "searching does not show the search results" || exit 1
    dump --tab starred --query refund --find-file "$found" | want "Starred matching · 2" "the Starred tab does not filter the search" || exit 1
    printf '{"schema":"chainlog.menu/1","query":"zzzz","results":[]}' > "$tmp/none.json"
    dump --query zzzz --find-file "$tmp/none.json" | want "No session matches" "a search with no hits has no empty state" || exit 1
    dump --query zzzz --find-file /dev/null | want "Search is not available" "a failed search is not reported" || exit 1
    "$bin" --dump-problem notfound | want "chainlog was not found" "the not-found state is wrong" || exit 1
    "$bin" --print-script "echo hi" | head -1 | grep -q -- " -il$" || die "check failed: the terminal script is not an interactive login shell"
    dump --hover-first | want "preview: Shop PR 4402 invoice emails — Emails go out twice" "hovering a row does not show its preview" || exit 1
    dump --tab chains | want "enter opens: Shop PR 4321 refund, round 3 (r3)" "Enter on the Chains tab does not open the newest round" || exit 1
    "$bin" --selftest "$here/sample.json" > "$tmp/selftest.txt" || { cat "$tmp/selftest.txt" >&2; die "check failed: the app's own checks (Chainlog --selftest sample.json)"; }
    tail -1 "$tmp/selftest.txt"
    printf 'menubar check ok\n'
    ;;
  --shots)
    [ -n "${2:-}" ] || die "usage: sh build.sh --shots DIR"
    mkdir -p "$2"
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/chainlog-menubar.XXXXXX")
    trap 'rm -rf "$tmp"' EXIT INT TERM
    build "$tmp"
    bin="$tmp/Chainlog.app/Contents/MacOS/Chainlog"
    for mode in light dark; do
      flag=; [ "$mode" = dark ] && flag=--dark
      "$bin" --render "$2/menubar-sessions-$mode.png" $flag --dump "$here/sample.json" >/dev/null || die "could not draw the popover (needs a macOS desktop session)"
      "$bin" --render "$2/menubar-search-$mode.png" $flag --dump "$here/sample.json" --find-file "$here/sample-find.json" --query refund >/dev/null || die "could not draw the search"
    done
    printf 'wrote %s/menubar-{sessions,search}-{light,dark}.png\n' "$2"
    ;;
  build)
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/chainlog-menubar.XXXXXX")
    build "$tmp"
    printf '%s\n' "$tmp/Chainlog.app"
    ;;
  *) die "usage: sh build.sh [--install | --uninstall | --check]" ;;
esac
