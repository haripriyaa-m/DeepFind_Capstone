#!/usr/bin/env bash
# =============================================================================
#  DeepFind 0.3.0 — desktop-app installer for Windows, as a shell script
#
#  Run it on the Windows laptop from either
#    * Git Bash   (comes with "Git for Windows")   ->  bash install-deepfind.sh
#    * WSL        (Ubuntu etc. on Windows)          ->  bash install-deepfind.sh
#
#  What it does (same result as DeepFind-Setup-0.3.0-online.exe):
#    1. copies the app (payload/) into  %LOCALAPPDATA%\Programs\DeepFind
#    2. downloads a private Python 3.11 runtime (~40 MB, python-build-standalone)
#    3. installs the exact pinned libraries (~250 MB, requirements-windows.lock.txt)
#    4. adds Desktop + Start-menu shortcuts and an "Apps & features" entry
#    5. starts DeepFind
#  No admin rights. Your index/settings in %LOCALAPPDATA%\DeepFind are never touched.
#
#  UPDATING: run the script from a NEWER kit on a laptop that already has DeepFind.
#  It notices the existing install and only
#    * replaces the app files that actually changed (a few MB at most),
#    * reuses the Python runtime (no 40 MB download),
#    * skips the libraries entirely when requirements-windows.lock.txt is unchanged,
#      otherwise downloads only the packages whose pinned version changed.
#  So after the first install, an update is just the small kit zip (~5 MB).
#
#  Options:   --no-launch      don't start DeepFind at the end
#             --no-desktop     no Desktop shortcut
#             --dir <path>     install somewhere else (Windows or bash path)
#             --uninstall      remove DeepFind (asks before deleting your index)
# =============================================================================
set -euo pipefail

APP="DeepFind"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20240814/cpython-3.11.9+20240814-x86_64-pc-windows-msvc-shared-install_only.tar.gz"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="$HERE/payload"
VERSION="$(tr -d '[:space:]' < "$PAYLOAD/app/VERSION" 2>/dev/null || echo '?')"

# Git Bash rewrites arguments that look like /paths (e.g. reg.exe /v /d /f) — switch that off.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

LAUNCH=1; DESKTOP=1; UNINSTALL=0; DIR_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-launch)  LAUNCH=0 ;;
    --no-desktop) DESKTOP=0 ;;
    --uninstall)  UNINSTALL=1 ;;
    --dir)        shift; DIR_ARG="${1:-}" ;;
    -h|--help)    sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

step() { printf '\n\033[1;34m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- environment
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ENV=gitbash ;;
  Linux)  if grep -qi microsoft /proc/version 2>/dev/null; then ENV=wsl; else ENV=linux; fi ;;
  *)      ENV=other ;;
esac
if [ "$ENV" = linux ] || [ "$ENV" = other ]; then
  die "This script installs the WINDOWS app, so it has to run on Windows (Git Bash or WSL).
       On a plain Linux/macOS machine use the source code instead (see README.txt)."
fi

if [ "$ENV" = gitbash ]; then
  to_unix() { cygpath -u "$1"; }
  to_win()  { cygpath -w "$1"; }
  winenv()  { printenv "$1" || true; }          # Git Bash inherits the Windows environment
else
  to_unix() { wslpath -u "$1"; }
  to_win()  { wslpath -w "$1"; }
  winenv()  { (cd /mnt/c 2>/dev/null; cmd.exe /c "echo %$1%" 2>/dev/null) | tr -d '\r'; }
fi
for t in cmd.exe powershell.exe reg.exe; do
  command -v "$t" >/dev/null || [ "$ENV" = gitbash ] || die "$t not reachable from WSL (is Windows interop enabled?)"
done
PS() { powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$1" | tr -d '\r'; }

LOCALAPPDATA_W="$(winenv LOCALAPPDATA)"
[ -n "$LOCALAPPDATA_W" ] && [ "$LOCALAPPDATA_W" != "%LOCALAPPDATA%" ] || die "couldn't read %LOCALAPPDATA%"
if [ -n "$DIR_ARG" ]; then
  case "$DIR_ARG" in [A-Za-z]:\\*|[A-Za-z]:/*) INST="$(to_unix "$DIR_ARG")" ;; *) INST="$DIR_ARG" ;; esac
else
  INST="$(to_unix "$LOCALAPPDATA_W")/Programs/$APP"
fi
mkdir -p "$INST"
INST="$(cd "$INST" && pwd)"
INST_W="$(to_win "$INST")"
cd "$INST"                     # a Windows folder as working dir (WSL: avoids cmd.exe UNC-path warnings)
DATA_W="$LOCALAPPDATA_W\\$APP"

close_app() {
  if tasklist.exe 2>/dev/null | tr -d '\r' | grep -qi '^DeepFind'; then
    echo "    DeepFind is running - closing it."
    taskkill.exe /F /IM DeepFind.exe /T >/dev/null 2>&1 || true
    taskkill.exe /F /IM DeepFind-console.exe /T >/dev/null 2>&1 || true
    sleep 2
  fi
}

UNKEY='HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DeepFind'

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  step "Uninstalling $APP from $INST_W"
  close_app
  PS "Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Desktop')) 'DeepFind.lnk'), (Join-Path ([Environment]::GetFolderPath('Programs')) 'DeepFind.lnk')" || true
  rm -rf "$INST/app" "$INST/runtime"
  rm -f "$INST/DeepFind.exe" "$INST/DeepFind-console.exe" "$INST/Uninstall DeepFind.cmd" "$INST/Uninstall.exe"
  rmdir "$INST" 2>/dev/null || true
  reg.exe delete "$UNKEY" /f >/dev/null 2>&1 || true
  reg.exe delete 'HKCU\Software\DeepFind' /f >/dev/null 2>&1 || true
  ok "App removed."
  read -r -p "    Also delete your index, settings and AI models ($DATA_W)? [y/N] " a || a=n
  if [ "${a,,}" = y ]; then rm -rf "$(to_unix "$DATA_W")"; ok "Data deleted."; fi
  exit 0
fi

# ---------------------------------------------------------------- install
cat <<EOF

  ____                  _____ _           _
 |  _ \\  ___  ___ _ __ |  ___(_)_ __   __| |
 | | | |/ _ \\/ _ \\ '_ \\| |_  | | '_ \\ / _\` |
 | |_| |  __/  __/ |_) |  _| | | | | | (_| |
 |____/ \\___|\\___| .__/|_|   |_|_| |_|\\__,_|   $VERSION
                 |_|      offline semantic file search

  Installing to: $INST_W
  (running from $ENV)
EOF
OLD_VER=""
if [ -f "$INST/app/VERSION" ] && [ -f "$INST/runtime/python.exe" ]; then
  OLD_VER="$(tr -d '[:space:]' < "$INST/app/VERSION")"
  printf '\n  \033[1mDeepFind %s is already installed -> UPDATE MODE (to %s)\033[0m\n' "$OLD_VER" "$VERSION"
  echo "  Only changed files are replaced; the Python runtime and libraries are reused."
fi

[ -f "$PAYLOAD/DeepFind.exe" ] && [ -f "$PAYLOAD/app/desktop/main.py" ] \
  || die "payload/ folder not found next to this script. Unzip the WHOLE zip first, then run the script from inside it."

step "1/5  Checking this PC"
ARCH="$(winenv PROCESSOR_ARCHITECTURE)"
case "$ARCH" in AMD64|ARM64) ok "Windows $ARCH" ;; *) die "DeepFind needs 64-bit Windows (found: $ARCH)" ;; esac
[ "$ARCH" = ARM64 ] && echo "    (ARM laptop: DeepFind runs under Windows' x64 emulation - slower, but works)"
WV2=""
for k in 'HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' \
         'HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' \
         'HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'; do
  v="$(reg.exe query "$k" /v pv 2>/dev/null | tr -d '\r' | awk '/pv/ {print $NF}')" || true
  if [ -n "$v" ] && [ "$v" != "0.0.0.0" ]; then WV2="$v"; break; fi
done
if [ -n "$WV2" ]; then ok "Microsoft Edge WebView2 $WV2"
else
  echo "    WebView2 runtime NOT found - DeepFind's window needs it."
  echo "    Opening the download page: install the 'Evergreen Bootstrapper', then start DeepFind."
  cmd.exe /c start "" "https://developer.microsoft.com/microsoft-edge/webview2/#download-section" >/dev/null 2>&1 || true
fi
close_app

step "2/5  App files"
# copy only what differs (byte compare), then delete files the new version no longer has
changed=0; added=0; removed=0
while IFS= read -r -d '' f; do
  rel="${f#"$PAYLOAD"/}"; dst="$INST/$rel"
  if [ ! -f "$dst" ]; then
    mkdir -p "$(dirname "$dst")"; cp -f "$f" "$dst"; added=$((added + 1))
  elif ! cmp -s "$f" "$dst"; then
    cp -f "$f" "$dst"; changed=$((changed + 1))
  fi
done < <(find "$PAYLOAD/app" "$PAYLOAD/DeepFind.exe" "$PAYLOAD/DeepFind-console.exe" -type f -print0)
while IFS= read -r -d '' f; do
  rel="${f#"$INST"/}"
  [ -e "$PAYLOAD/$rel" ] || { rm -f "$f"; removed=$((removed + 1)); }
done < <(find "$INST/app" -type f -not -path "*/__pycache__/*" -print0)   # keep compiled caches
find "$INST/app" -depth -type d -empty -delete 2>/dev/null || true
ok "$changed changed, $added new, $removed removed"

step "3/5  Python runtime"
if [ -f "$INST/runtime/python311.dll" ] && [ -f "$INST/runtime/python.exe" ]; then
  ok "already installed - reusing it"
else
  rm -rf "$INST/runtime" "$INST/python"
  TMP="$INST/.download"; mkdir -p "$TMP"
  echo "    downloading (~40 MB)..."
  if command -v curl >/dev/null; then DL() { curl -L --fail --retry 3 --retry-delay 3 -# -o "$TMP/python.tar.gz" "$PY_URL"; }
  else DL() { curl.exe -L --fail --retry 3 --retry-delay 3 -# -o "$(to_win "$TMP/python.tar.gz")" "$PY_URL"; }; fi
  DL \
    || die "couldn't download the Python runtime - check the internet connection and run the script again."
  echo "    unpacking..."
  tar -xzf "$TMP/python.tar.gz" -C "$INST" || die "couldn't unpack the Python runtime"
  mv "$INST/python" "$INST/runtime"
  rm -rf "$TMP"
  [ -f "$INST/runtime/python311.dll" ] || die "Python runtime is incomplete"
  ok "Python 3.11 ready"
fi

PYEXE="$INST/runtime/python.exe"
LOCK="$INST/app/requirements-windows.lock.txt"
MARK="$INST/runtime/.deepfind-libs.lock"     # copy of the lock file the libraries were installed from
if [ -f "$MARK" ] && cmp -s "$LOCK" "$MARK"; then
  step "4/5  Libraries"
  ok "unchanged since the last install - nothing to download"
else
if [ -n "$OLD_VER" ]; then step "4/5  Libraries (only changed packages are downloaded)"
else step "4/5  Libraries (~250 MB the first time - a few minutes)"; fi
if [ ! -f "$INST/runtime/Lib/site-packages/pip/__init__.py" ]; then   # full-installer copies ship without pip
  echo "    getting pip..."
  curl -L --fail --retry 3 -sS -o "$INST/get-pip.py" https://bootstrap.pypa.io/get-pip.py \
    && "$PYEXE" "$(to_win "$INST/get-pip.py")" --disable-pip-version-check --no-warn-script-location; rm -f "$INST/get-pip.py"
fi
"$PYEXE" -m pip install --disable-pip-version-check --no-warn-script-location --prefer-binary \
    --no-cache-dir -r "$(to_win "$INST/app/requirements-windows.lock.txt")" \
  || die "couldn't install the libraries - check the internet connection and run the script again
       (it continues where it stopped)."
"$PYEXE" -c "import webview, lancedb, fastapi, fitz, onnxruntime; print('    all libraries import fine')" \
  | tr -d '\r' || die "libraries installed but don't import - run DeepFind-console.exe in $INST_W for details"
cp -f "$LOCK" "$MARK"
fi

step "5/5  Shortcuts + Apps & features entry"
EXE_W="$INST_W\\DeepFind.exe"
cat > "$INST/Uninstall DeepFind.cmd" <<EOF
@echo off
rem DeepFind uninstaller (created by install-deepfind.sh)
taskkill /F /IM DeepFind.exe /T >nul 2>&1
taskkill /F /IM DeepFind-console.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul
del /q "%USERPROFILE%\\Desktop\\DeepFind.lnk" "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\DeepFind.lnk" 2>nul
powershell -NoProfile -Command "Remove-Item -Force -EA 0 (Join-Path ([Environment]::GetFolderPath('Desktop')) 'DeepFind.lnk')"
reg delete "$UNKEY" /f >nul 2>&1
reg delete "HKCU\\Software\\DeepFind" /f >nul 2>&1
choice /M "Also delete your DeepFind index, settings and AI models"
if errorlevel 2 goto keep
rmdir /s /q "%LOCALAPPDATA%\\DeepFind"
:keep
echo DeepFind was removed.
cd /d "%TEMP%" & (goto) 2>nul & rmdir /s /q "%~dp0"
EOF
sed -i 's/$/\r/' "$INST/Uninstall DeepFind.cmd"

PS "\$s = New-Object -ComObject WScript.Shell
\$targets = @([Environment]::GetFolderPath('Programs'))
if ($DESKTOP -eq 1) { \$targets += [Environment]::GetFolderPath('Desktop') }
foreach (\$d in \$targets) {
  \$l = \$s.CreateShortcut((Join-Path \$d 'DeepFind.lnk'))
  \$l.TargetPath = '$EXE_W'; \$l.WorkingDirectory = '$INST_W'
  \$l.IconLocation = '$EXE_W,0'; \$l.Description = 'Search your files offline'; \$l.Save()
}" || echo "    (couldn't create shortcuts - start $EXE_W directly)"
ok "Start menu$( [ $DESKTOP = 1 ] && echo ' + Desktop') shortcut"

reg.exe add 'HKCU\Software\DeepFind' /v InstallDir /d "$INST_W" /f >/dev/null   # lets in-app updates replace this copy
reg.exe add "$UNKEY" /v DisplayName     /d "$APP" /f >/dev/null
reg.exe add "$UNKEY" /v DisplayVersion  /d "$VERSION" /f >/dev/null
reg.exe add "$UNKEY" /v Publisher       /d "DeepFind capstone team" /f >/dev/null
reg.exe add "$UNKEY" /v DisplayIcon     /d "$EXE_W" /f >/dev/null
reg.exe add "$UNKEY" /v InstallLocation /d "$INST_W" /f >/dev/null
reg.exe add "$UNKEY" /v UninstallString /d "\"$INST_W\\Uninstall DeepFind.cmd\"" /f >/dev/null
reg.exe add "$UNKEY" /v NoModify /t REG_DWORD /d 1 /f >/dev/null
reg.exe add "$UNKEY" /v NoRepair /t REG_DWORD /d 1 /f >/dev/null
ok "listed in Settings > Apps (uninstall from there)"

if [ -n "$OLD_VER" ] && [ "$OLD_VER" = "$VERSION" ]; then printf '\n\033[1;32m  DeepFind %s was already up to date (checked + repaired).\033[0m\n' "$VERSION"
elif [ -n "$OLD_VER" ]; then printf '\n\033[1;32m  DeepFind updated: %s -> %s.\033[0m\n' "$OLD_VER" "$VERSION"
else printf '\n\033[1;32m  DeepFind %s is installed.\033[0m\n' "$VERSION"; fi
echo "  Open it from the Desktop or Start menu. First start takes ~10 s."
echo "  For meaning-based search (images, scanned PDFs): Settings > AI models > Download."
if [ "$LAUNCH" = 1 ]; then
  (cd "$INST" && cmd.exe /c start "" "$EXE_W" >/dev/null 2>&1) || true
fi
