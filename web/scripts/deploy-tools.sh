#!/usr/bin/env bash
# Build the web app and publish it to tools.3030.ca/op1loop.
#
# Copies web/dist into the tools.3030.ca repo, commits only html/op1loop,
# pushes, and pulls on the server. Set TOOLS_REPO if the repo lives elsewhere.
set -euo pipefail

WEB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS_REPO="${TOOLS_REPO:-$HOME/tools.3030.ca}"
TARGET="$TOOLS_REPO/html/op1loop"
SERVER="root@140.82.19.43"

cd "$WEB_DIR"
npm test
npm run build

rm -rf "$TARGET"
mkdir -p "$TARGET"
cp -R dist/. "$TARGET/"

cd "$TOOLS_REPO"
git add html/op1loop
if git diff --cached --quiet -- html/op1loop; then
  echo "op1loop is already up to date"
else
  VERSION="$(node -p "require('$WEB_DIR/package.json').version")"
  REV="$(git -C "$WEB_DIR" rev-parse --short HEAD)"
  git commit -m "op1loop: OP-1 Loop Extractor web app v$VERSION ($REV)" -- html/op1loop
fi
git push origin main
ssh "$SERVER" 'cd /opt/tools.3030.ca && git pull --ff-only'
echo "Live at https://tools.3030.ca/op1loop/"
