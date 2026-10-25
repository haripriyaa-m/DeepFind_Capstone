#!/usr/bin/env bash
# Build the small install/update kit:  dist/DeepFind-<ver>-kit.zip  (~5 MB)
#
#   DeepFind-<ver>-kit/
#     install-deepfind.sh               install OR update from Git Bash / WSL on Windows
#     DeepFind-Setup-<ver>-online.exe   same thing as a double-click installer (no bash needed)
#     payload/                          the app: DeepFind.exe, DeepFind-console.exe, app\
#     README.txt
#
# Neither the Python runtime nor the libraries are inside: they are downloaded once on the first
# install and reused by every later update. That is why the same small zip serves as the update.
# Run after packaging/build_windows.sh (it calls this script at the end).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-$(tr -d '[:space:]' < "$ROOT/VERSION")}"
STAGE="${BUILD_DIR:-$ROOT/build/windows}/DeepFind"
DIST="$ROOT/dist"
KIT="DeepFind-$VERSION-kit"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
[ -f "$STAGE/DeepFind.exe" ] || { echo "run packaging/build_windows.sh first"; exit 1; }

K="$TMP/$KIT"; mkdir -p "$K/payload"
cp "$STAGE/DeepFind.exe" "$STAGE/DeepFind-console.exe" "$K/payload/"
cp -r "$STAGE/app" "$K/payload/"
find "$K/payload" -type d -name __pycache__ -prune -exec rm -rf {} +   # Windows compiles these on first start
cp "$ROOT/packaging/windows/install-deepfind.sh" "$K/"
[ -f "$DIST/DeepFind-Setup-$VERSION-online.exe" ] && cp "$DIST/DeepFind-Setup-$VERSION-online.exe" "$K/"
sed "s/@VERSION@/$VERSION/g" "$ROOT/packaging/windows/KIT-README.txt" | sed 's/$/\r/' > "$K/README.txt"
rm -f "$DIST/$KIT.zip"
( cd "$TMP" && zip -qr -9 "$DIST/$KIT.zip" "$KIT" )
ls -lh "$DIST/$KIT.zip"
