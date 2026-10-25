; DeepFind — Windows installer (NSIS 3, Modern UI 2)
; Built by packaging/build_windows.sh:  makensis -DVERSION=<ver> -DSTAGE=<dir> -DOUTFILE=<exe> deepfind.nsi
;
; -DONLINE builds the small "online" installer: it ships only the app (~3 MB) and downloads
; the Python runtime + pinned libraries (requirements-windows.lock.txt) during installation,
; using curl.exe / tar.exe that come with Windows 10 (1803+) and 11.
;
; Per-user install (no admin prompt) into %LOCALAPPDATA%\Programs\DeepFind.
; User data (%LOCALAPPDATA%\DeepFind: index, settings, models, logs) is kept on
; uninstall unless the user chooses to remove it.
;
; In-app update: the running app downloads this same installer and starts it as
;   DeepFind-Setup-<ver>.exe /UPDATE
; which skips the welcome/folder/finish pages (only the progress page shows), waits for the
; old app to exit, installs over the existing folder and starts the new version.

Target amd64-unicode          ; the app is x64-only; a 64-bit installer also sees the real registry view
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ManifestDPIAware true
RequestExecutionLevel user

!ifndef VERSION
  !define VERSION "0.3.0"
!endif
!ifndef STAGE
  !error "pass -DSTAGE=<staged app folder>"
!endif
!ifndef OUTFILE
  !ifdef ONLINE
    !define OUTFILE "DeepFind-Setup-${VERSION}-online.exe"
  !else
    !define OUTFILE "DeepFind-Setup-${VERSION}.exe"
  !endif
!endif

!define APP      "DeepFind"
!define PY_URL   "https://github.com/astral-sh/python-build-standalone/releases/download/20240814/cpython-3.11.9+20240814-x86_64-pc-windows-msvc-shared-install_only.tar.gz"
!define EXE      "DeepFind.exe"
!define UNINST   "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"
!define WV2KEY   "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
!define WV2KEYU  "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

Name "${APP}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
BrandingText "${APP} ${VERSION} — offline semantic file search"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "FileDescription" "${APP} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "DeepFind capstone team"
VIAddVersionKey "LegalCopyright" "DeepFind capstone team"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!insertmacro GetParameters
!insertmacro GetOptions

Var IsUpdate        ; 1 when started by the app's updater (/UPDATE)

Function SkipIfUpdate
  ${If} $IsUpdate = 1
    Abort
  ${EndIf}
FunctionEnd

!define MUI_ICON   "${STAGE}\app\desktop\deepfind.ico"
!define MUI_UNICON "${STAGE}\app\desktop\deepfind.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Install ${APP} ${VERSION}"
!ifdef ONLINE
!define MUI_WELCOMEPAGE_TEXT "${APP} searches the files on this PC by their words and, once the optional AI models are downloaded, by their meaning.$\r$\n$\r$\nThis small installer downloads the rest of ${APP} (about 300 MB) while installing, so keep the internet connected. No administrator rights are needed.$\r$\n$\r$\nClick Next to continue."
!else
!define MUI_WELCOMEPAGE_TEXT "${APP} searches the files on this PC by their words and, once the optional AI models are downloaded, by their meaning — including what's inside images and scanned PDFs.$\r$\n$\r$\nEverything runs offline on your computer. No administrator rights are needed.$\r$\n$\r$\nClick Next to continue."
!endif
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APP}"

!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfUpdate
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfUpdate
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfUpdate
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; The app holds the named mutex below for as long as its process lives (desktop/main.py), which is
; exactly when python311.dll and the other runtime files are locked. During an update the app is
; closing itself, so wait for it quietly (up to 60 s) instead of asking.
!macro ENSURE_CLOSED ID
  StrCpy $R9 0
  ensure_${ID}:
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\DeepFind.Desktop.SingleInstance") p .r0'
  ${If} $0 <> 0
    System::Call 'kernel32::CloseHandle(p r0)'
  ${EndIf}
  FindWindow $1 "" "${APP}"
  ${If} $0 <> 0
  ${OrIf} $1 <> 0
    ${If} $IsUpdate = 1
    ${AndIf} $R9 < 120
      IntOp $R9 $R9 + 1
      Sleep 500
      Goto ensure_${ID}
    ${EndIf}
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${APP} is running. Close it and click Retry." /SD IDCANCEL IDRETRY ensure_${ID}
    Abort
  ${EndIf}
!macroend

Function .onInit
  ${GetParameters} $R0
  ${GetOptions} $R0 "/UPDATE" $R1
  ${IfNot} ${Errors}
    StrCpy $IsUpdate 1
  ${EndIf}
  ClearErrors
  ${If} $IsUpdate = 1
    Return                         ; WebView2 was there when the app last ran; no prompts on updates
  ${EndIf}
  ; WebView2 runtime ships with Windows 11 and current Windows 10; warn if it is missing.
  ReadRegStr $0 HKLM "${WV2KEY}" "pv"
  ${If} $0 == ""
    ReadRegStr $0 HKCU "${WV2KEYU}" "pv"
  ${EndIf}
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    MessageBox MB_YESNO|MB_ICONINFORMATION "${APP} needs the Microsoft Edge WebView2 Runtime, which was not found.$\r$\n$\r$\nOpen the download page now? (You can install it after ${APP}.)" /SD IDNO IDNO +2
    ExecShell "open" "https://developer.microsoft.com/microsoft-edge/webview2/#download-section"
  ${EndIf}
FunctionEnd

!ifdef ONLINE
Function DownloadRuntime
  InitPluginsDir
  ${IfNot} ${FileExists} "$SYSDIR\curl.exe"
  ${OrIfNot} ${FileExists} "$SYSDIR\tar.exe"
    MessageBox MB_ICONSTOP "This installer needs Windows 10 (version 1803 or newer) or Windows 11.$\r$\nUse the full offline installer instead." /SD IDOK
    Abort
  ${EndIf}

  ${If} ${FileExists} "$INSTDIR\runtime\python311.dll"
  ${AndIf} ${FileExists} "$INSTDIR\runtime\python.exe"
    DetailPrint "Python runtime already present - checking libraries only."
    Goto libs
  ${EndIf}
  RMDir /r "$INSTDIR\runtime"
  DetailPrint "Downloading the Python runtime (about 40 MB)..."
  nsExec::ExecToLog '"$SYSDIR\curl.exe" -L --fail --retry 3 --retry-delay 3 -sS -o "$PLUGINSDIR\python.tar.gz" "${PY_URL}"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Couldn't download the Python runtime (error $0).$\r$\nCheck your internet connection and run the installer again." /SD IDOK
    Abort
  ${EndIf}
  DetailPrint "Unpacking the runtime..."
  nsExec::ExecToLog '"$SYSDIR\tar.exe" -xzf "$PLUGINSDIR\python.tar.gz" -C "$INSTDIR"'
  Pop $0
  Rename "$INSTDIR\python" "$INSTDIR\runtime"
  ${IfNot} ${FileExists} "$INSTDIR\runtime\python311.dll"
    MessageBox MB_ICONSTOP "Couldn't unpack the Python runtime (error $0)." /SD IDOK
    Abort
  ${EndIf}

  libs:
  ; Same pinned libraries as the installed copy? Then there is nothing to download (typical update).
  ${If} ${FileExists} "$INSTDIR\runtime\.deepfind-libs.lock"
    nsExec::Exec '"$SYSDIR\fc.exe" /b "$INSTDIR\runtime\.deepfind-libs.lock" "$INSTDIR\app\requirements-windows.lock.txt"'
    Pop $0
    ${If} $0 == 0
      DetailPrint "Libraries unchanged - nothing to download."
      Return
    ${EndIf}
  ${EndIf}
  ; A copy installed with the full installer has no pip (it is stripped to save space): fetch it once.
  ${IfNot} ${FileExists} "$INSTDIR\runtime\Lib\site-packages\pip\__init__.py"
    DetailPrint "Getting the package installer (pip)..."
    nsExec::ExecToLog '"$SYSDIR\curl.exe" -L --fail --retry 3 -sS -o "$PLUGINSDIR\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"'
    Pop $0
    nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$PLUGINSDIR\get-pip.py" --disable-pip-version-check --no-warn-script-location'
    Pop $0
  ${EndIf}
  DetailPrint "Installing libraries (about 250 MB on first install; only changed ones on updates)..."
  nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" -m pip install --disable-pip-version-check --no-warn-script-location --prefer-binary --progress-bar off --no-cache-dir -r "$INSTDIR\app\requirements-windows.lock.txt"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Couldn't download the libraries DeepFind needs (error $0).$\r$\nCheck your internet connection and run the installer again." /SD IDOK
    Abort
  ${EndIf}
  CopyFiles /SILENT "$INSTDIR\app\requirements-windows.lock.txt" "$INSTDIR\runtime\.deepfind-libs.lock"
  DetailPrint "All components installed."
FunctionEnd
!endif

Section "DeepFind" SecMain
  SectionIn RO
  !insertmacro ENSURE_CLOSED inst
  ; replace old app files but never touch user data
  RMDir /r "$INSTDIR\app"
!ifndef ONLINE
  RMDir /r "$INSTDIR\runtime"
!endif
  SetOutPath "$INSTDIR"
  File "${STAGE}\${EXE}"
  File "${STAGE}\DeepFind-console.exe"
!ifndef ONLINE
  File /r "${STAGE}\runtime"
!endif
  File /r "${STAGE}\app"
!ifdef ONLINE
  Call DownloadRuntime
!endif

  CreateDirectory "$SMPROGRAMS"
  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${EXE}" "" "$INSTDIR\${EXE}" 0 SW_SHOWNORMAL "" "Search your files offline"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${EXE}" "" "$INSTDIR\${EXE}" 0

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST}" "Publisher" "DeepFind capstone team"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINST}" "EstimatedSize" $0
SectionEnd

; After an in-app update, start the new version (a normal install offers "Launch" on the finish page).
Function .onInstSuccess
  ${If} $IsUpdate = 1
    Exec '"$INSTDIR\${EXE}"'
  ${EndIf}
FunctionEnd

Section "Uninstall"
  !insertmacro ENSURE_CLOSED uninst
  Delete "$SMPROGRAMS\${APP}.lnk"
  Delete "$DESKTOP\${APP}.lnk"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\DeepFind-console.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINST}"
  DeleteRegKey HKCU "Software\${APP}"
  IfSilent done
  MessageBox MB_YESNO|MB_ICONQUESTION "Also delete your DeepFind index, settings and downloaded AI models?$\r$\n$\r$\n($LOCALAPPDATA\${APP})" IDNO done
  RMDir /r "$LOCALAPPDATA\${APP}"
  done:
SectionEnd
