#!/bin/sh
# Build, install or remove the chainlog menu bar app (macOS only; needs the Xcode command line tools).
#   sh build.sh              build into a temporary folder and say where it is
#   sh build.sh --install    build, copy to ~/Applications/Chainlog.app (or $CHAINLOG_APP_DIR) and open it
#   sh build.sh --uninstall  quit the app and delete it
#   sh build.sh --check      build, then print the menu from sample.json (a smoke test; no window)
set -eu

here=$(cd "$(dirname "$0")" && pwd)
dir="${CHAINLOG_APP_DIR:-$HOME/Applications}"
app="$dir/Chainlog.app"
version=$(cat "$here/../VERSION")

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
  sed "s/__VERSION__/$version/g" "$here/Info.plist" > "$out/Contents/Info.plist"
  swiftc -Onone -swift-version 5 -parse-as-library -o "$out/Contents/MacOS/Chainlog" "$here/Chainlog.swift" \
    -framework AppKit -framework ServiceManagement 2>&1 | grep -v "^$" || true
  [ -x "$out/Contents/MacOS/Chainlog" ] || die "the build failed (see the messages above)."
  codesign --force --sign - "$out" >/dev/null 2>&1 || true   # ad-hoc: lets macOS run it and keep its login item
}

case "${1:-build}" in
  --install)
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/chainlog-menubar.XXXXXX")
    trap 'rm -rf "$tmp"' EXIT INT TERM
    build "$tmp"
    quit_running
    mkdir -p "$dir"
    rm -rf "$app"
    cp -R "$tmp/Chainlog.app" "$app"
    open "$app"
    printf 'installed %s\n' "$app"
    ;;
  --uninstall)
    quit_running
    rm -rf "$app"
    printf 'removed %s\n' "$app"
    ;;
  --check)
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/chainlog-menubar.XXXXXX")
    trap 'rm -rf "$tmp"' EXIT INT TERM
    build "$tmp"
    "$tmp/Chainlog.app/Contents/MacOS/Chainlog" --dump "$here/sample.json"
    ;;
  build)
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/chainlog-menubar.XXXXXX")
    build "$tmp"
    printf '%s\n' "$tmp/Chainlog.app"
    ;;
  *) die "usage: sh build.sh [--install | --uninstall | --check]" ;;
esac
