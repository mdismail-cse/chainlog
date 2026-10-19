#!/bin/sh
# chainlog installer
#   curl -fsSL https://raw.githubusercontent.com/mdismail-cse/chainlog/main/install.sh | sh
#   … | sh -s -- --no-setup            install the CLI only (agents: run `chainlog setup` after asking)
#   … | sh -s -- --version v0.1.0      pin a release (default: the latest release, else the main branch)
#   … | sh -s -- --version main         the latest main branch
#   CHAINLOG_SOURCE=/path/to/checkout sh install.sh   install from a local checkout (offline / dev)
# Installs into ${CHAINLOG_HOME:-~/.local/share/chainlog}/app/<version> and links ~/.local/bin/chainlog.
# Never touches ~/.claude or ~/.codex itself; `chainlog setup` does that, showing a diff and asking first.
set -eu

REPO="mdismail-cse/chainlog"
VERSION="latest"
SETUP=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-setup) SETUP=0 ;;
    --version) shift; VERSION="$1" ;;
    --version=*) VERSION="${1#*=}" ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
die() { printf 'chainlog install: %s\n' "$*" >&2; exit 1; }

DATA="${CHAINLOG_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/chainlog}"
BIN="$HOME/.local/bin"

# 1. python3 >= 3.9 (the macOS system python 3.9 is enough; no packages needed)
PY=$(command -v python3 || true)
[ -n "$PY" ] || die "python3 not found. macOS: xcode-select --install (or brew install python). Linux: install python3."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
  || die "python3 is $("$PY" -c 'import platform; print(platform.python_version())'); chainlog needs 3.9+."

umask 077
mkdir -p "$DATA/app" "$BIN"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/chainlog-install.XXXXXX")
trap 'rm -rf "$TMP"' EXIT INT TERM

if [ -n "${CHAINLOG_SOURCE:-}" ]; then
  # 2a. local checkout
  [ -f "$CHAINLOG_SOURCE/plugin/VERSION" ] || die "$CHAINLOG_SOURCE is not a chainlog checkout"
  TAG="v$(cat "$CHAINLOG_SOURCE/plugin/VERSION")"
  mkdir -p "$TMP/src"
  (cd "$CHAINLOG_SOURCE" && tar cf - --exclude '__pycache__' --exclude '*.pyc' .claude-plugin plugin install.sh INSTALL.md README.md LICENSE 2>/dev/null) \
    | (cd "$TMP/src" && tar xf -)
else
  # 2b. tagged release tarball, verified against checksums.txt
  if [ "$VERSION" = latest ]; then
    TAG=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
      | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
    [ -n "$TAG" ] || VERSION=main   # no release published yet: use the main branch
  else
    TAG="$VERSION"
  fi
  mkdir -p "$TMP/src"
  if [ "$VERSION" = main ]; then
    say "No release published yet — installing the latest main branch (no checksum file for branch snapshots)."
    curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/main" -o "$TMP/chainlog.tar.gz" \
      || die "download failed: https://github.com/$REPO (main)"
    tar xzf "$TMP/chainlog.tar.gz" -C "$TMP/src" --strip-components 1
    TAG="v$(cat "$TMP/src/plugin/VERSION")-main"
  else
    URL="https://github.com/$REPO/releases/download/$TAG"
    say "Downloading chainlog $TAG…"
    curl -fsSL "$URL/chainlog-$TAG.tar.gz" -o "$TMP/chainlog.tar.gz" || die "download failed: $URL/chainlog-$TAG.tar.gz"
    curl -fsSL "$URL/checksums.txt" -o "$TMP/checksums.txt" || die "download failed: $URL/checksums.txt"
    want=$(grep " chainlog-$TAG.tar.gz\$" "$TMP/checksums.txt" | cut -d' ' -f1)
    if command -v shasum >/dev/null 2>&1; then got=$(shasum -a 256 "$TMP/chainlog.tar.gz" | cut -d' ' -f1)
    else got=$(sha256sum "$TMP/chainlog.tar.gz" | cut -d' ' -f1); fi
    [ -n "$want" ] && [ "$want" = "$got" ] || die "checksum mismatch for chainlog-$TAG.tar.gz"
    tar xzf "$TMP/chainlog.tar.gz" -C "$TMP/src" --strip-components 1
  fi
fi

# 3. unpack to app/<tag>, point app/current at it, link the launcher
DEST="$DATA/app/$TAG"
rm -rf "$DEST"
mv "$TMP/src" "$DEST"
chmod 755 "$DEST/plugin/bin/chainlog" "$DEST/plugin/hooks/spool.sh"
ln -sfn "$DEST" "$DATA/app/current"
ln -sfn "$DATA/app/current/plugin/bin/chainlog" "$BIN/chainlog"
say "✓ chainlog $TAG installed: $BIN/chainlog"
case ":$PATH:" in *":$BIN:"*) ;; *) say "! $BIN is not on your PATH. Add to ~/.zshrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;; esac

# 4. index existing sessions (read-only), then the guided setup
"$BIN/chainlog" scan || true
if [ "$SETUP" = 1 ] && (exec </dev/tty) 2>/dev/null; then   # a real terminal we can ask in
  say ""
  say "Next: chainlog setup shows every change it would make and asks before each one."
  "$BIN/chainlog" setup </dev/tty || true
else
  say "Next, type:  chainlog setup    (a few yes/no questions; press Enter for yes)"
fi
