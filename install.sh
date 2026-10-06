#!/usr/bin/env bash
# how-good-am-i installer.
#   curl -fsSL https://raw.githubusercontent.com/AaronBergman/how-good-am-i/main/install.sh | bash
# Uninstall:
#   curl -fsSL https://raw.githubusercontent.com/AaronBergman/how-good-am-i/main/install.sh | bash -s -- --uninstall
set -euo pipefail

REPO="AaronBergman/how-good-am-i"
BRANCH="${HGAI_BRANCH:-main}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "how-good-am-i needs python3 (3.8+). Install it first (macOS: xcode-select --install or brew install python)." >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || { echo "python3 is older than 3.8" >&2; exit 1; }

# Running from a checkout? Use it. Piped from curl? Download the repo tarball.
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/installer/install.py" ]; then
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar -xz -C "$TMP"
  SRC="$TMP/$(ls "$TMP" | head -1)"
fi

python3 "$SRC/installer/install.py" "$@"
