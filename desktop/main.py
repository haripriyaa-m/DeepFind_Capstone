"""
desktop/main.py — the DeepFind Windows desktop shell.

One process does everything:
  * starts the FastAPI engine (backend/main.py) on a free 127.0.0.1 port, in a thread;
  * opens a native window (pywebview -> Microsoft Edge WebView2) on the built React UI,
    which the engine serves from the same origin;
  * exposes a tiny JS bridge (folder picker, confirm dialog, open data/log folders);
  * shuts the engine down cleanly when the window closes.

User data lives in %LOCALAPPDATA%\\DeepFind (index, settings, models, logs), so the app
folder itself can stay read-only.

Run from source:   python desktop/main.py            (needs: pip install pywebview)
Headless engine:   python desktop/main.py --headless (no window; prints the URL — used by build smoke tests)
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
from pathlib import Path

APP_NAME = "DeepFind"
ROOT = Path(__file__).resolve().parent.parent          # the project (app\ in the install)
try:
    APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"   # bump VERSION to release
except OSError:
    APP_VERSION = "0.0.0"
IS_WIN = sys.platform.startswith("win")


# ---------------------------------------------------------------------------
# Environment — must be set BEFORE the engine modules are imported
# ---------------------------------------------------------------------------

def _user_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") if IS_WIN else os.environ.get("XDG_DATA_HOME")
    return Path(base or Path.home() / ".local" / "share") / APP_NAME


USER_DIR = Path(os.environ.get("DEEPFIND_HOME", _user_dir()))
DATA_DIR = USER_DIR / "data"
# models bundled next to the app (WITH_MODELS=1 build) win; otherwise per-user download
MODELS_DIR = ROOT / "models" if (ROOT / "models").is_dir() else USER_DIR / "models"
LOG_DIR = USER_DIR / "logs"


def _prepare_env():
    for d in (DATA_DIR, MODELS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DEEPFIND_DATA_DIR", str(DATA_DIR))
    os.environ.setdefault("DEEPFIND_DB_PATH", str(DATA_DIR / "lancedb"))
    os.environ.setdefault("DEEPFIND_MODELS_DIR", str(MODELS_DIR))
    os.environ.setdefault("DEEPFIND_CLIP_CACHE_DIR", str(MODELS_DIR / "clip_cache"))
    os.environ.setdefault("DEEPFIND_OCR_MODELS_DIR", str(MODELS_DIR / "ocr_en"))
    os.environ.setdefault("DEEPFIND_UI_DIR", str(ROOT / "frontend" / "dist"))
    # The engine never reaches the network by itself; the "Download models" button
    # runs a separate downloader process without this flag.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _setup_logging():
    # pythonw / embedded GUI processes have no console: stdout/stderr may be None.
    log_file = LOG_DIR / "deepfind.log"
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    gui = os.environ.get("DEEPFIND_LAUNCHER") == "1" or sys.stdout is None or sys.stderr is None
    if gui:
        # started from DeepFind.exe (no console): keep prints/tracebacks in a file
        stream = open(LOG_DIR / "console.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
    else:
        root.addHandler(logging.StreamHandler())
    return log_file


# ---------------------------------------------------------------------------
# Windows niceties (all best-effort: never fatal)
# ---------------------------------------------------------------------------

_mutex = None


def _single_instance() -> bool:
    """False if another DeepFind is already running (two engines must not share one index)."""
    global _mutex
    if not IS_WIN:
        return True
    import ctypes
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\DeepFind.Desktop.SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


def _focus_existing_window():
    if not IS_WIN:
        return
    import ctypes
    hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)                 # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    else:
        ctypes.windll.user32.MessageBoxW(None, "DeepFind is already running.", APP_NAME, 0x40)


def _app_user_model_id():
    """Own taskbar identity (icon + pinning) instead of being grouped under Python."""
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeepFind.Desktop")
        except Exception:
            pass


def _our_window_hwnd():
    """Our top-level window, found with plain Win32 calls (never .NET from a Python thread:
    pythonnet keeps the GIL while a cross-thread WinForms call waits for the UI thread, and
    the UI thread may be waiting for the GIL -> the window freezes)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid, found = os.getpid(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(128)
            user32.GetWindowTextW(hwnd, buf, 128)
            if buf.value == APP_NAME:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def _style_window():
    """Light title bar on Windows 10 20H1+ / 11 (DWM attributes, via ctypes only)."""
    if not IS_WIN:
        return
    try:
        import ctypes
        from ctypes import wintypes
        for _ in range(20):                                   # the window may take a moment
            hwnd = _our_window_hwnd()
            if hwnd:
                break
            time.sleep(0.25)
        else:
            return
        dwm = ctypes.windll.dwmapi

        def attr(key, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), key, ctypes.byref(v), ctypes.sizeof(v))

        attr(20, 1)                    # DWMWA_USE_IMMERSIVE_DARK_MODE
        attr(35, 0x00FFFFFF)           # DWMWA_CAPTION_COLOR  #FFFFFF (0x00BBGGRR)
        attr(36, 0x0033231B)           # DWMWA_TEXT_COLOR     #1B2333
        attr(34, 0x00DEE5E1)           # DWMWA_BORDER_COLOR   #E1E5EE
    except Exception:
        logging.getLogger("deepfind.desktop").debug("title bar styling skipped", exc_info=True)


def _webview2_installed() -> bool:
    """Same check pywebview makes; without WebView2 it would silently fall back to the
    Internet Explorer engine, which can't run the UI."""
    if not IS_WIN:
        return True
    import winreg
    key = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive, path in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}"),
                       (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}"),
                       (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}")):
        try:
            with winreg.OpenKey(hive, path) as k:
                pv, _ = winreg.QueryValueEx(k, "pv")
                if pv and pv != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


LOADING_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>DeepFind</title>
<style>
html,body{margin:0;height:100%;background:#f4f6fa;color:#1b2333;font:15px/1.5 "Segoe UI",system-ui,sans-serif;overflow:hidden}
.w{height:100%;display:flex;flex-direction:column;justify-content:center;padding:0 9vw}
h1{margin:0;font:700 clamp(40px,7vw,72px)/1 "Segoe UI",system-ui,sans-serif;letter-spacing:-.02em;color:#2646d6}
p{margin:22px 0 0;font:italic 18px Georgia,serif;color:#4a5568}
.l{width:280px;height:3px;margin-top:22px;background:repeating-linear-gradient(90deg,#2646d6 0 7px,transparent 7px 16px);animation:m .6s linear infinite}
@keyframes m{to{background-position:16px 0}}
code{font:12px Consolas,monospace;color:#b42318}
</style></head><body><div class="w"><h1>DeepFind</h1>
<p id="n">Starting the search engine&hellip;</p><div class="l" id="l"></div></div>
<script>
var url = "__URL__", t0 = Date.now();
function tick() {
  fetch(url + "api/health", {mode: "no-cors", cache: "no-store"})
    .then(function () { location.replace(url); })
    .catch(function () {
      var s = (Date.now() - t0) / 1000;
      if (s > 8) document.getElementById("n").textContent = "Still starting \u2014 the first launch takes longer while Windows checks the new files\u2026";
      if (s > 150) { document.getElementById("n").innerHTML = "The engine didn't start. See <code>__LOG__</code>"; document.getElementById("l").style.display = "none"; return; }
      setTimeout(tick, 400);
    });
}
tick();
</script></body></html>"""


def _serve_error_page(port: int, message: str, log_file):
    """If the engine can't start, answer on its port with a readable error page, so the
    loading screen (which is polling that port) shows the reason instead of spinning."""
    import html
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    body = (f"<!doctype html><meta charset='utf-8'><title>DeepFind</title><body style='margin:0;background:#f4f6fa;"
            f"color:#1b2333;font:15px/1.6 Segoe UI,sans-serif;padding:8vh 9vw'><h1 style='font:700 44px Segoe UI,sans-serif;"
            f"color:#b42318;margin:0'>Couldn't start</h1><p>DeepFind's search engine failed to start:</p>"
            f"<pre style='white-space:pre-wrap;color:#b42318'>{html.escape(message)}</pre>"
            f"<p>Log file: <code>{html.escape(str(log_file))}</code><br>Close this window and open DeepFind again. "
            f"If it keeps happening, run <b>DeepFind-console.exe</b> from the install folder to see details.</p>").encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True, name="error-page").start()
    except Exception:
        logging.getLogger("deepfind.desktop").exception("could not serve the error page")


# ---------------------------------------------------------------------------
# Engine (uvicorn in a background thread)
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Engine:
    def __init__(self, port: int = 0):
        import uvicorn
        from backend.main import app          # heavy imports (lancedb, numpy…) happen here
        self.port = port or _free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_config=None,
                                log_level="warning", access_log=False, lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="engine")

    def start(self, timeout: float = 60.0):
        self.thread.start()
        end = time.time() + timeout
        while not self.server.started:
            if not self.thread.is_alive():
                raise RuntimeError("The engine stopped while starting — see deepfind.log")
            if time.time() > end:
                raise TimeoutError("The engine did not start within 60 s")
            time.sleep(0.05)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=15)


# ---------------------------------------------------------------------------
# JS bridge  (window.pywebview.api.*)
# ---------------------------------------------------------------------------

class Bridge:
    """Public methods are callable from the page. Private attributes (leading _) are not
    exposed, which matters: pywebview would otherwise try to serialise the window object."""

    def __init__(self):
        self._window = None

    def pick_folders(self):
        import webview
        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=True)
        return [str(p) for p in picked] if picked else []

    def confirm(self, message, title=APP_NAME):
        return bool(self._window.create_confirmation_dialog(title, message))

    def app_info(self):
        return {"version": APP_VERSION, "data_dir": str(DATA_DIR), "models_dir": str(MODELS_DIR),
                "log_dir": str(LOG_DIR), "platform": sys.platform}

    def open_url(self, url):
        """Open an https link in the user's browser (used for the release page)."""
        import webbrowser
        return bool(str(url).startswith("https://") and webbrowser.open(str(url)))

    def open_path(self, which):
        target = {"logs": LOG_DIR, "data": DATA_DIR, "models": MODELS_DIR}.get(which)
        if not target:
            return False
        target.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            os.startfile(str(target))                            # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", str(target)])
        return True


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="DeepFind")
    ap.add_argument("--headless", action="store_true", help="run the engine only (no window)")
    ap.add_argument("--debug", action="store_true", help="enable WebView2 dev tools")
    args = ap.parse_args(argv)

    _prepare_env()
    log_file = _setup_logging()
    log = logging.getLogger("deepfind.desktop")
    log.info("DeepFind %s starting (python %s, %s) root=%s", APP_VERSION, sys.version.split()[0], sys.platform, ROOT)

    if not _single_instance():
        log.info("another instance is running — focusing it")
        _focus_existing_window()
        return 0
    _app_user_model_id()

    if args.headless:
        try:
            engine = Engine()
            engine.start()
        except Exception:
            log.exception("engine failed to start")
            return 1
        log.info("engine listening on %s", engine.url)
        from backend.updater import updater
        updater.set_quit_hook(lambda: setattr(engine.server, "should_exit", True))
        print(f"DEEPFIND_URL={engine.url}", flush=True)
        try:
            while engine.thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        engine.stop()
        return 0

    if not _webview2_installed():
        log.error("Microsoft Edge WebView2 runtime not found")
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "DeepFind needs the Microsoft Edge WebView2 Runtime, which isn't installed on this PC.\n\n"
                      "Click OK to open the download page, install the 'Evergreen Bootstrapper', then start DeepFind again.",
                APP_NAME, 0x40)
            os.startfile("https://developer.microsoft.com/microsoft-edge/webview2/#download-section")  # type: ignore[attr-defined]
        return 1

    # The window opens right away on a loading screen; the engine (heavy imports, index,
    # watcher) starts in the background and the page switches over when it answers.
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    holder: dict = {}

    def boot():
        try:
            engine = Engine(port)
            engine.start(timeout=180)
            holder["engine"] = engine
            log.info("engine listening on %s", url)
        except Exception as e:
            log.exception("engine failed to start")
            _serve_error_page(port, f"{type(e).__name__}: {e}", log_file)

    threading.Thread(target=boot, daemon=True, name="boot").start()

    import webview
    bridge = Bridge()
    page = LOADING_HTML.replace("__URL__", url).replace("__LOG__", str(log_file).replace("\\", "\\\\"))
    window = webview.create_window(
        APP_NAME, html=page, js_api=bridge,
        width=1280, height=820, min_size=(900, 600),
        background_color="#F4F6FA", text_select=False,
    )
    bridge._window = window
    from backend.updater import updater      # in-app updates: how to close the app for the installer
    updater.set_quit_hook(window.destroy)    # window closes -> engine.stop() below -> process ends
    window.events.shown += lambda: _style_window()
    try:
        webview.start(
            gui="edgechromium" if IS_WIN else None,
            debug=args.debug,
            private_mode=False,                                   # keep localStorage prefs between runs
            storage_path=str(USER_DIR / "webview"),
        )
    finally:
        log.info("window closed — stopping engine")
        if "engine" in holder:
            holder["engine"].stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
