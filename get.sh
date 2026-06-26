#!/usr/bin/env bash
#
# PC Remote bootstrap — downloads the repo and runs the interactive install.sh.
# Usage (on the Raspberry Pi, as a NORMAL user, not root):
#
#     curl -fsSL https://raw.githubusercontent.com/kwiato/pimote/master/get.sh | bash
#
# Pick a different branch/tag:  PIMOTE_REF=v1.0 ... | bash

set -euo pipefail

REPO="kwiato/pimote"
REF="${PIMOTE_REF:-master}"
TARBALL="https://github.com/$REPO/archive/refs/heads/$REF.tar.gz"

printf '\033[36m•\033[0m PC Remote — downloading %s@%s…\n' "$REPO" "$REF"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if command -v curl >/dev/null; then
    curl -fsSL "$TARBALL" | tar -xz -C "$TMP"
elif command -v wget >/dev/null; then
    wget -qO- "$TARBALL" | tar -xz -C "$TMP"
else
    echo "curl or wget required." >&2; exit 1
fi

SRC="$(find "$TMP" -maxdepth 1 -type d -name 'pimote-*' | head -1)"
[ -n "$SRC" ] || { echo "Failed to unpack the repo." >&2; exit 1; }
chmod +x "$SRC/install.sh"

# install.sh is interactive (asks for MAC/password). When this bootstrap runs
# via `curl | bash`, stdin is the pipe, not the keyboard — so we attach the real
# terminal from /dev/tty so `read` works.
if [ -e /dev/tty ]; then
    bash "$SRC/install.sh" < /dev/tty
else
    echo "No terminal (/dev/tty). Clone the repo and run ./install.sh manually." >&2
    exit 1
fi
