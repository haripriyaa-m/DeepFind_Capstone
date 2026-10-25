#!/usr/bin/env bash
# Build the DeepFind Windows installer + portable zip — from Linux, WSL or CI.
#
#   packaging/build_windows.sh              -> dist/DeepFind-Setup-<ver>.exe, dist/DeepFind-<ver>-portable.zip
#   WITH_MODELS=1 packaging/build_windows.sh   also bundles ./models (run scripts/download_models.py first)
#
# Needs: bash, curl, python3 + pip, node/npm, mingw-w64 (x86_64-w64-mingw32-gcc/windres), nsis (makensis), zip.
#   Ubuntu/Debian/WSL:  sudo apt install mingw-w64 nsis zip
#
# How it works (no Windows machine, no PyInstaller, no Rust needed):
#   1. a relocatable CPython 3.11 for Windows (python-build-standalone) becomes  runtime\
#   2. pip cross-installs win_amd64 wheels of every dependency into runtime\Lib\site-packages
#   3. the React UI is built and copied with the Python sources into  app\
#   4. a tiny native DeepFind.exe (MinGW) loads runtime\python311.dll and runs app\desktop\main.py
#   5. NSIS packs it all into a per-user installer
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-$(tr -d '[:space:]' < "$ROOT/VERSION")}"   # bump the VERSION file to release
PY_VER="3.11.9"
PBS_TAG="20240814"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PY_VER}+${PBS_TAG}-x86_64-pc-windows-msvc-shared-install_only.tar.gz"
BUILD="${BUILD_DIR:-$ROOT/build/windows}"
STAGE="$BUILD/DeepFind"
CACHE="${CACHE_DIR:-$ROOT/build/cache}"
DIST="$ROOT/dist"
WIN="$ROOT/packaging/windows"
PURE_SDISTS="proxy_tools"

say() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing tool: $1 ($2)"; exit 1; }; }
need curl "curl"; need npm "Node.js"; need python3 "Python 3"
need x86_64-w64-mingw32-gcc "apt install mingw-w64"; need makensis "apt install nsis"; need zip "apt install zip"

rm -rf "$STAGE"; mkdir -p "$STAGE" "$CACHE" "$DIST"

say "1/6  Windows Python runtime ($PY_VER)"
TGZ="$CACHE/python-$PY_VER-win64.tar.gz"
[ -s "$TGZ" ] || curl -fL --retry 3 -o "$TGZ" "$PY_URL"
tar -xzf "$TGZ" -C "$BUILD"
mv "$BUILD/python" "$STAGE/runtime"
# not needed at runtime
rm -rf "$STAGE/runtime/include" "$STAGE/runtime/libs" "$STAGE/runtime/Lib/test" \
       "$STAGE/runtime/Lib/idlelib" "$STAGE/runtime/Lib/tkinter" "$STAGE/runtime/tcl" \
       "$STAGE/runtime/Lib/ensurepip" "$STAGE/runtime/Lib/site-packages/pip"* \
       "$STAGE/runtime/Lib/site-packages/setuptools"* "$STAGE/runtime/Scripts"

say "2/6  win_amd64 wheels -> runtime\\Lib\\site-packages"
# a clean venv for the build tooling (distro-patched pip/setuptools can't build some sdists)
[ -x "$CACHE/venv/bin/python" ] || python3 -m venv "$CACHE/venv"
PIP="$CACHE/venv/bin/python -m pip"
$PIP install --quiet --disable-pip-version-check --upgrade pip setuptools wheel
# a few pure-Python deps are published as sdists only: build them into universal wheels first
WH="$CACHE/wheelhouse"; mkdir -p "$WH"
$PIP wheel --quiet --disable-pip-version-check --no-deps -w "$WH" $PURE_SDISTS
$PIP install --quiet --disable-pip-version-check --no-warn-script-location \
  --target "$STAGE/runtime/Lib/site-packages" \
  --platform win_amd64 --python-version 3.11 --implementation cp --abi cp311 \
  --only-binary=:all: --find-links "$WH" --cache-dir "$CACHE/pip" \
  -r "$WIN/requirements-windows.lock.txt"      # exact pinned versions: same libraries as the online installer
SP="$STAGE/runtime/Lib/site-packages"
rm -rf "$SP/bin" "$SP"/*.dist-info/RECORD
find "$SP/numpy" -type d -name tests -prune -exec rm -rf {} + 2>/dev/null || true
find "$SP" -type d -name "__pycache__" -prune -exec rm -rf {} +

say "3/6  React UI"
( cd "$ROOT/frontend" && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build )

say "4/6  application files"
APP="$STAGE/app"
mkdir -p "$APP/frontend" "$APP/scripts" "$APP/desktop" "$APP/backend"
cp "$ROOT"/{ingestion_service,search_engine,vision_service,video_frames,pipeline}.py "$APP/"
cp "$ROOT"/backend/*.py "$APP/backend/"
cp "$ROOT"/desktop/*.py "$ROOT/desktop/deepfind.ico" "$APP/desktop/"
cp "$ROOT/scripts/download_models.py" "$ROOT/scripts/fetch_model.py" "$APP/scripts/"
cp -r "$ROOT/frontend/dist" "$APP/frontend/dist"
cp "$ROOT/README.md" "$ROOT/CHANGES.md" "$ROOT/update_source.json" "$APP/"
cp "$WIN/requirements-windows.lock.txt" "$APP/"      # used by the online installer + install scripts
cp "$WIN/requirements-windows.lock.txt" "$STAGE/runtime/.deepfind-libs.lock"   # "these libraries are installed" (updates skip pip)
printf '%s\n' "$VERSION" > "$APP/VERSION"      # what the app reports and compares against new releases
if [ "${WITH_MODELS:-0}" = "1" ]; then
  [ -d "$ROOT/models" ] || { echo "WITH_MODELS=1 but ./models is missing"; exit 1; }
  echo "bundling models/ (the app reads app\\models in place — no download needed)"
  cp -r "$ROOT/models" "$APP/models"
fi
# byte-compile for a faster first start (.pyc is version-specific, not OS-specific)
python3 - <<'PY' "$STAGE"
import sys, compileall, pathlib
root = pathlib.Path(sys.argv[1])
def comp(path):
    try:
        compileall.compile_dir(path, quiet=1, workers=0)     # parallel
    except Exception:                                        # no process pool here (some sandboxes) -> serial
        compileall.compile_dir(path, quiet=1, workers=1)
comp(str(root / "app"))
comp(str(root / "runtime" / "Lib"))
PY

say "5/6  DeepFind.exe launcher"
( cd "$WIN" && x86_64-w64-mingw32-windres launcher.rc -O coff -o "$BUILD/launcher.res" )
x86_64-w64-mingw32-gcc -O2 -s -municode -mwindows -static-libgcc "$WIN/launcher.c" "$BUILD/launcher.res" \
  -o "$STAGE/DeepFind.exe" -lshell32
# same launcher with a console window + live log output, for troubleshooting
x86_64-w64-mingw32-gcc -O2 -s -municode -mconsole -static-libgcc -DCONSOLE_BUILD "$WIN/launcher.c" "$BUILD/launcher.res" \
  -o "$STAGE/DeepFind-console.exe" -lshell32

say "6/6  installer + portable zip"
OUT="$DIST/DeepFind-Setup-$VERSION.exe"
makensis -V2 -DVERSION="$VERSION" -DSTAGE="$STAGE" -DOUTFILE="$OUT" "$WIN/deepfind.nsi"
# small installer (~3 MB) that downloads runtime + libraries during setup
makensis -V2 -DVERSION="$VERSION" -DSTAGE="$STAGE" -DONLINE -DOUTFILE="$DIST/DeepFind-Setup-$VERSION-online.exe" "$WIN/deepfind.nsi"
( cd "$BUILD" && rm -f "$DIST/DeepFind-$VERSION-portable.zip" && zip -qr -9 "$DIST/DeepFind-$VERSION-portable.zip" DeepFind )

# small install/update kit (shell script + online installer + app payload)
bash "$ROOT/packaging/make_kit.sh"

du -sh "$STAGE" | sed 's/^/installed size: /'
ls -lh "$DIST"
say "done"
