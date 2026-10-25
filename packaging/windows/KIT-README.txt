DeepFind @VERSION@ - install / update kit for Windows 10 and 11 (64-bit)
=====================================================================

FIRST: right-click the zip > "Extract All...". Do not run anything from inside the zip.

OPTION A - shell script (Git Bash or WSL)
  1. Open "Git Bash" in this folder (right-click in the folder > "Open Git Bash here").
  2. Type:   bash install-deepfind.sh
  It downloads the Python runtime (~40 MB) and libraries (~250 MB) ONCE,
  then adds DeepFind to the Desktop and Start menu and opens it.

OPTION B - no Git Bash on this laptop
  Double-click DeepFind-Setup-@VERSION@-online.exe. Same result.

UPDATING ANOTHER LAPTOP LATER (no big download again)
  Copy the NEW kit zip (about 5 MB) to the laptop, extract it, and run
  OPTION A or B again. DeepFind sees the existing install and:
    - replaces only the app files that changed,
    - keeps the Python runtime,
    - downloads no libraries unless the new version changed one
      (then only that package),
    - keeps your index, folders and settings.
  The script prints e.g. "UPDATE MODE (0.3.0 -> 0.3.1)" and "4 changed, 1 new, 0 removed".

  If the project is on GitHub (update_source.json) and a release was published,
  DeepFind can also update itself: Settings > Updates. It downloads the small
  online installer (~3 MB), not the full one.

Other commands
  bash install-deepfind.sh --no-launch     install without starting it
  bash install-deepfind.sh --no-desktop    no Desktop shortcut
  bash install-deepfind.sh --uninstall     remove (asks before deleting your index)
  Or: Windows Settings > Apps > DeepFind > Uninstall.

Needs: Windows 10 1803+ or 11, 64-bit, internet for the first install,
Microsoft Edge WebView2 Runtime (built into Windows 11; the script checks).
Installs to %LOCALAPPDATA%\Programs\DeepFind - no administrator rights.
If the window stays blank: run DeepFind-console.exe in that folder to see the log.
