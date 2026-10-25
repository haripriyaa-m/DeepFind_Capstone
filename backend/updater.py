"""
backend/updater.py — in-app updates for the Windows build.

How it works (the same idea as Chrome / VS Code, minus a server of our own):
  1. A GitHub *Release* holds the installer:  DeepFind-Setup-<version>.exe  (+ a .sha256 file).
  2. The app asks GitHub for the latest release, compares its version with the VERSION file
     it was built with, and shows "Update available" in Settings.
  3. "Update" downloads the installer to %LOCALAPPDATA%\\DeepFind\\updates, checks its SHA-256,
     starts it (`/UPDATE`: progress page only, no questions) and closes the app. The installer
     waits for DeepFind to exit, replaces app\\ and runtime\\, never touches your index/settings,
     and starts the new version.

Where releases come from is set in `update_source.json` next to this package's parent folder
(app\\update_source.json in an install):  {"repo": "owner/name"}.
Overrides for testing: DEEPFIND_UPDATE_REPO, DEEPFIND_UPDATE_URL (a JSON manifest, see _from_manifest).

Nothing here runs unless the user triggers it or "Check for updates automatically" is on
(once a day, a single request to github.com).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("deepfind.update")

ROOT = Path(__file__).resolve().parent.parent
IS_WIN = sys.platform.startswith("win")
ASSET_RE = re.compile(r"^DeepFind-Setup-(\d+(?:\.\d+)*)(?:-online)?\.exe$")
# An installed copy already has the Python runtime + libraries, so updates use the small "-online"
# installer (~3 MB: new app files; it reuses runtime\ and pip only fetches libraries whose pinned
# version changed). The full ~180 MB installer is only the fallback.
ONLINE_SUFFIX = "-online.exe"
CHECK_EVERY = 24 * 3600          # automatic check at most once a day
NOTES_MAX = 1500


class UpdateError(Exception):
    pass


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

def read_version() -> str:
    """The version this build was made from (the VERSION file in the project / app folder)."""
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(v: str) -> tuple[int, ...]:
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", v or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return bool(a) and a > b


# ---------------------------------------------------------------------------
# where do releases live?
# ---------------------------------------------------------------------------

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def source() -> dict:
    cfg: dict = {}
    try:
        cfg = json.loads((ROOT / "update_source.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    default = (cfg.get("repo") or "").strip()
    repo = (os.environ.get("DEEPFIND_UPDATE_REPO") or get_repo_override() or default).strip()
    manifest = (os.environ.get("DEEPFIND_UPDATE_URL") or cfg.get("manifest_url") or "").strip()
    if repo.upper() == "OWNER/REPO" or not _REPO_RE.match(repo):
        repo = ""
    if default.upper() == "OWNER/REPO" or not _REPO_RE.match(default):
        default = ""
    return {"repo": repo, "manifest_url": manifest, "default_repo": default}


def normalize_repo(text: str) -> str:
    """'owner/name', 'github.com/owner/name' or a full https://github.com/owner/name[.git][/...] link
    -> 'owner/name'. Empty -> ''. Anything else raises UpdateError."""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", t, flags=re.I)
    parts = [x for x in t.split("?")[0].split("#")[0].split("/") if x]
    if len(parts) >= 2:
        repo = f"{parts[0]}/{parts[1]}"
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        if _REPO_RE.match(repo) and repo.upper() != "OWNER/REPO":
            return repo
    raise UpdateError("Enter the repository as owner/name, or paste its GitHub link "
                      "(for example https://github.com/owner/name).")


def repo_path() -> Path:
    return _home() / "update_repo"


def get_repo_override() -> str:
    """The repository the user chose in Settings -> Updates ('' = use the one built into the app)."""
    try:
        return repo_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_repo(text: str) -> str:
    repo = normalize_repo(text)
    p = repo_path()
    if not repo:
        p.unlink(missing_ok=True)
        return ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(repo, encoding="utf-8")
    return repo


# ---- private repositories: a read-only GitHub token, entered once per PC in Settings -> Updates ----
def _home() -> Path:
    base = os.environ.get("DEEPFIND_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share"), "DeepFind")
    return Path(os.environ.get("DEEPFIND_UPDATES_DIR", str(Path(base) / "updates")))


def token_path() -> Path:
    return _home() / "github_token"


def get_token() -> str:
    t = os.environ.get("DEEPFIND_UPDATE_TOKEN", "").strip()
    if t:
        return t
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_token(token: str) -> None:
    token = (token or "").strip()
    if token and not re.fullmatch(r"[A-Za-z0-9_\-]{20,255}", token):
        raise UpdateError("That doesn't look like a GitHub token (it should start with github_pat_ or ghp_).")
    p = token_path()
    if not token:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token, encoding="utf-8")


class _AuthRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub answers an asset download with a redirect to a signed storage URL; the token
    must not travel there (the storage server rejects requests that carry two kinds of auth)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


def _urlopen(url: str, timeout: float = 15, accept: str = "application/vnd.github+json, */*"):
    headers = {"User-Agent": f"DeepFind/{read_version()}", "Accept": accept}
    tok = get_token()
    if tok and urllib.parse.urlsplit(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {tok}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    opener = urllib.request.build_opener(_AuthRedirect)
    return opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)


def _get_json(url: str) -> dict:
    with _urlopen(url) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_text(url: str) -> str:
    with _urlopen(url) as r:
        return r.read(4096).decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _latest_tag_without_api(repo: str) -> str:
    """github.com/<repo>/releases/latest redirects to .../tag/<tag>. Unlike api.github.com this
    has no tight per-IP rate limit (a shared campus / office IP can exhaust the API's 60/hour)."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(urllib.request.Request(f"https://github.com/{repo}/releases/latest",
                                           headers={"User-Agent": f"DeepFind/{read_version()}"}), timeout=15)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "") if e.code in (301, 302, 303, 307, 308) else ""
        m = re.search(r"/releases/tag/([^/?#]+)$", loc)
        if m:
            return urllib.parse.unquote(m.group(1))
        raise
    raise UpdateError("No published release was found.")


def _sha_from_text(text: str) -> str:
    m = re.search(r"\b([A-Fa-f0-9]{64})\b", text or "")
    return m.group(1).lower() if m else ""


def _from_github(rel: dict) -> dict:
    assets = [a for a in rel.get("assets", []) if ASSET_RE.match(a.get("name", ""))]
    asset = next((a for a in assets if a["name"].endswith(ONLINE_SUFFIX)), assets[0] if assets else None)
    if not asset:
        raise UpdateError("The latest release has no DeepFind-Setup-<version>.exe file attached.")
    sha = ""
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        sha = digest[7:].lower()
    if not sha:
        side = next((a for a in rel.get("assets", []) if a.get("name") == asset["name"] + ".sha256"), None)
        if side:
            try:
                sha = _sha_from_text(_get_text(side["url"] if get_token() and side.get("url") else side["browser_download_url"]))
            except Exception:
                logger.warning("could not read %s", side.get("name"))
    version = (rel.get("tag_name") or "").lstrip("vV") or ASSET_RE.match(asset["name"]).group(1)
    private = bool(get_token() and asset.get("url"))      # private repo: download through the API with the token
    return {"version": version, "url": asset["url"] if private else asset["browser_download_url"],
            "api_download": private, "name": asset["name"],
            "size": int(asset.get("size") or 0), "sha256": sha,
            "notes": (rel.get("body") or "")[:NOTES_MAX], "page": rel.get("html_url") or "",
            "published": rel.get("published_at") or ""}


def _from_tag(repo: str, tag: str) -> dict:
    """API-less fallback: the asset URL is predictable from the tag."""
    version = tag.lstrip("vV")
    base = f"https://github.com/{repo}/releases/download/{tag}"
    name, sha = f"DeepFind-Setup-{version}.exe", ""
    for cand in (f"DeepFind-Setup-{version}{ONLINE_SUFFIX}", f"DeepFind-Setup-{version}.exe"):   # small one first
        try:
            sha = _sha_from_text(_get_text(f"{base}/{cand}.sha256"))
        except Exception:
            continue
        if sha:
            name = cand
            break
    return {"version": version, "url": f"{base}/{name}", "name": name, "size": 0, "sha256": sha,
            "notes": "", "page": f"https://github.com/{repo}/releases/tag/{tag}", "published": ""}


def _from_manifest(m: dict) -> dict:
    """{"version": "0.3.0", "url": "https://.../DeepFind-Setup-0.3.0.exe", "sha256": "...",
        "size": 123, "notes": "...", "page": "https://..."} — for hosting updates somewhere else."""
    url = m.get("url", "")
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not m.get("version") or not ASSET_RE.match(name):
        raise UpdateError("The update manifest is incomplete.")
    return {"version": str(m["version"]).lstrip("vV"), "url": url, "name": name,
            "size": int(m.get("size") or 0), "sha256": _sha_from_text(m.get("sha256", "")),
            "notes": str(m.get("notes", ""))[:NOTES_MAX], "page": m.get("page", ""), "published": ""}


def fetch_latest() -> dict:
    src = source()
    if src["manifest_url"]:
        return _from_manifest(_get_json(src["manifest_url"]))
    repo = src["repo"]
    try:
        return _from_github(_get_json(f"https://api.github.com/repos/{repo}/releases/latest"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429) and not get_token():    # API rate limit -> use the plain web redirect
            return _from_tag(repo, _latest_tag_without_api(repo))
        raise


def _friendly(e: Exception) -> str:
    if isinstance(e, UpdateError):
        return str(e)
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 404:
            return ("No published release was found. If the repository is private, add a read-only "
                    "access token under Settings -> Updates (and check the repository name)." if not get_token() else
                    "No release found. Check that the token can read this repository and that a release is published.")
        if e.code == 401:
            return "GitHub rejected the access token (expired or wrong). Enter a new one under Settings -> Updates."
        if e.code in (403, 429):
            return "GitHub is limiting requests from this network. Try again in a little while."
        return f"The update server answered with an error ({e.code})."
    if isinstance(e, (OSError, TimeoutError)):
        return "Couldn't reach the update server. Check your internet connection."
    return f"Update check failed ({type(e).__name__})."


# ---------------------------------------------------------------------------
# can this copy replace itself?
# ---------------------------------------------------------------------------

def _installed_dir() -> Optional[Path]:
    if not IS_WIN:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\DeepFind") as k:
            return Path(winreg.QueryValueEx(k, "InstallDir")[0])
    except OSError:
        return None


def self_update_blocker() -> str:
    """'' when this copy was installed by the installer; otherwise why it can't update itself."""
    if os.environ.get("DEEPFIND_ALLOW_DEV_UPDATE"):
        return ""
    if not IS_WIN:
        return "Automatic updates install on Windows only."
    inst = _installed_dir()
    if inst and os.path.normcase(str(inst.resolve())) == os.path.normcase(str(ROOT.parent.resolve())):
        return ""
    return "This copy is portable (not installed), so it can't replace itself. Download the new installer instead."


# ---------------------------------------------------------------------------
# the updater
# ---------------------------------------------------------------------------

class Updater:
    # idle | checking | up_to_date | available | downloading | ready | installing | error
    def __init__(self):
        self.current = read_version()
        self.state = "idle"
        self.error = ""
        self.latest: Optional[dict] = None
        self.checked_at = 0.0
        self.done = 0
        self.total = 0
        self._file: Optional[Path] = None
        self._lock = threading.RLock()
        self._quit: Optional[Callable[[], None]] = None
        self._just_updated = False
        self._bg_started = False
        self._load_memory()

    # ---- small persistent memory: last check time + the version we last ran ----
    def _dir(self) -> Path:
        base = os.environ.get("DEEPFIND_HOME") or os.path.join(
            os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share"), "DeepFind")
        return Path(os.environ.get("DEEPFIND_UPDATES_DIR", str(Path(base) / "updates")))

    def _mem_path(self) -> Path:
        return self._dir() / "state.json"

    def _load_memory(self):
        try:
            mem = json.loads(self._mem_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mem = {}
        self.checked_at = float(mem.get("checked_at", 0))
        seen = mem.get("last_run_version")
        self._just_updated = bool(seen) and is_newer(self.current, seen)
        self._save_memory(last_run_version=self.current)

    def _save_memory(self, **patch):
        try:
            self._dir().mkdir(parents=True, exist_ok=True)
            try:
                mem = json.loads(self._mem_path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                mem = {}
            mem.update(patch)
            self._mem_path().write_text(json.dumps(mem), encoding="utf-8")
        except OSError:
            logger.debug("could not save update state", exc_info=True)

    def clean_old(self):
        """Delete installers left over from earlier updates."""
        try:
            for f in self._dir().glob("DeepFind-Setup-*"):
                if self._file is None or f != self._file:
                    f.unlink(missing_ok=True)
        except OSError:
            pass

    # ---- public state ----
    def set_quit_hook(self, fn: Optional[Callable[[], None]]):
        self._quit = fn

    def info(self) -> dict:
        with self._lock:
            lat = self.latest
            available = bool(lat) and is_newer(lat["version"], self.current)
            return {
                "current": self.current,
                "state": self.state,
                "configured": bool(source()["repo"] or source()["manifest_url"]),
                "available": available,
                "latest": ({k: lat[k] for k in ("version", "notes", "page", "published", "size")} if lat else None),
                "checked_at": self.checked_at or None,
                "error": self.error,
                "progress": {"done": self.done, "total": self.total or (lat or {}).get("size", 0)},
                "can_self_update": self_update_blocker() == "",
                "blocker": self_update_blocker(),
                "just_updated": self._just_updated,
                "repo": source()["repo"],
                "repo_default": source()["default_repo"],
                "repo_custom": bool(get_repo_override()),
                "token_set": bool(get_token()),
            }

    def ack_updated(self):
        self._just_updated = False

    def set_repo(self, text: str) -> dict:
        """Point updates at another GitHub repository (empty = back to the built-in one)."""
        with self._lock:
            if self.state in ("downloading", "installing"):
                raise UpdateError("Wait for the current update to finish before changing the repository.")
            repo = set_repo(text)
            self.latest, self.state, self.error = None, "idle", ""
            self.done = self.total = 0
            self._file = None
            self.checked_at = 0.0                              # the daily check runs again soon
            self._save_memory(checked_at=0)
        logger.info("update repository set to %s", repo or "(built-in default)")
        return self.info()

    # ---- 1. check ----
    def check(self) -> dict:
        with self._lock:
            if self.state in ("checking", "downloading", "installing"):
                return self.info()
            if not (source()["repo"] or source()["manifest_url"]):
                self.state, self.error = "idle", "Updates aren't set up in this build (no update source)."
                return self.info()
            prev = self.state
            self.state, self.error = "checking", ""
        try:
            latest = fetch_latest()
            with self._lock:
                self.checked_at = time.time()
                self._save_memory(checked_at=self.checked_at)
                if prev == "ready" and self.latest and self.latest["version"] == latest["version"]:
                    self.state = "ready"                       # already downloaded
                else:
                    self.latest = latest
                    self.state = "available" if is_newer(latest["version"], self.current) else "up_to_date"
        except Exception as e:                                  # network, bad JSON, missing asset...
            logger.warning("update check failed: %s", e)
            with self._lock:
                self.state = prev if prev in ("available", "ready", "up_to_date") else "error"
                self.error = _friendly(e)
        return self.info()

    # ---- 2. download ----
    def download(self):
        with self._lock:
            if self.state == "downloading":
                return
            if self.state not in ("available", "error") or not self.latest or not is_newer(self.latest["version"], self.current):
                raise UpdateError("There is no update to download. Check for updates first.")
            if not self.latest["url"].startswith("https://"):
                raise UpdateError("The update isn't served over HTTPS; refusing to download it.")
            self.state, self.error, self.done, self.total = "downloading", "", 0, self.latest.get("size", 0)
            rel = dict(self.latest)
        threading.Thread(target=self._download, args=(rel,), daemon=True, name="update-download").start()

    def _download(self, rel: dict):
        dest = self._dir() / rel["name"]
        part = dest.with_name(dest.name + ".part")
        try:
            self._dir().mkdir(parents=True, exist_ok=True)
            self.clean_old()
            h = hashlib.sha256()
            accept = "application/octet-stream" if rel.get("api_download") else "*/*"
            with _urlopen(rel["url"], timeout=30, accept=accept) as r, open(part, "wb") as out:
                total = int(r.headers.get("Content-Length") or rel.get("size") or 0)
                with self._lock:
                    self.total = total
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    with self._lock:
                        self.done += len(chunk)
            size = part.stat().st_size
            if size < 1_000_000:
                raise UpdateError("The downloaded file is too small to be the installer.")
            if rel.get("size") and size != rel["size"]:
                raise UpdateError("The download is incomplete. Try again.")
            if rel.get("sha256") and h.hexdigest() != rel["sha256"]:
                raise UpdateError("The download is corrupted (checksum mismatch). Try again.")
            os.replace(part, dest)
            with self._lock:
                self._file, self.state = dest, "ready"
            logger.info("update %s downloaded (%s bytes, sha256 %s)", rel["version"], size,
                        "verified" if rel.get("sha256") else "not published")
        except Exception as e:
            logger.warning("update download failed: %s", e)
            part.unlink(missing_ok=True)
            with self._lock:
                self.state, self.error = "available", _friendly(e) if not isinstance(e, UpdateError) else str(e)

    # ---- 3. install (starts the installer, then the app closes itself) ----
    def install(self):
        with self._lock:
            if self.state != "ready" or not self._file or not self._file.is_file():
                raise UpdateError("The update hasn't been downloaded yet.")
            blocker = self_update_blocker()
            if blocker:
                raise UpdateError(blocker)
            if not ASSET_RE.match(self._file.name):
                raise UpdateError("Unexpected installer file name.")
            exe = self._file
            self.state = "installing"
        flags = 0x00000008 | 0x00000200 if IS_WIN else 0           # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen([str(exe), "/UPDATE"], close_fds=True, creationflags=flags, cwd=str(exe.parent))
        except OSError as e:
            with self._lock:
                self.state = "ready"
            raise UpdateError(f"Could not start the installer: {e}")
        logger.info("installer started (%s) — closing the app", exe)
        threading.Timer(1.0, self._close_app).start()               # let the HTTP reply reach the UI first

    def _close_app(self):
        try:
            if self._quit:
                self._quit()
                return
        except Exception:
            logger.exception("quit hook failed")
        os._exit(0)

    # ---- automatic checks ----
    def start_background(self, auto_enabled: Callable[[], bool], first_delay: float = 20.0):
        """Check once a day while the app is open (if the user left it on)."""
        if self._bg_started:
            return
        self._bg_started = True

        def loop():
            time.sleep(first_delay)
            while True:
                try:
                    if auto_enabled() and time.time() - self.checked_at >= CHECK_EVERY:
                        self.check()
                except Exception:
                    logger.debug("auto check crashed", exc_info=True)
                time.sleep(3600)

        threading.Thread(target=loop, daemon=True, name="update-auto").start()


updater = Updater()
