"""
backend/ocr_engines.py — which OCR engine DeepFind uses for images and scanned PDFs.

Kept deliberately light (no model imports) so the API can list engines, check whether they
are usable and switch between them without loading anything heavy. vision_service.py reads
the current choice from here on every OCR call, so a change takes effect immediately.

Engines
  rapidocr   built in. ONNX Runtime, CPU only, English models downloaded once.
  tesseract  the Tesseract program (https://github.com/UB-Mannheim/tesseract/wiki). It is a
             separate install, so it is looked up on PATH and in the usual Windows folders.
  off        no text is read from images (they are still found by what they show).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("deepfind.ocr_engines")

DEFAULT_ENGINE = "rapidocr"

ENGINES = [
    {"id": "rapidocr", "label": "RapidOCR",
     "description": "Built in and fast on a normal CPU. Uses the English models downloaded above."},
    {"id": "tesseract", "label": "Tesseract",
     "description": "The classic open-source OCR engine. Works on more fonts and scan types, "
                    "but it is a separate program that must be installed on this PC."},
    {"id": "off", "label": "Off",
     "description": "Don't read text from images. Pictures are still found by what they show."},
]
ENGINE_IDS = tuple(e["id"] for e in ENGINES)

TESSERACT_HELP = ("Tesseract is not installed on this PC. Press Install to add it, "
                  "or install it yourself and press Check again.")

_lock = threading.Lock()
_engine = os.environ.get("DEEPFIND_OCR_ENGINE", DEFAULT_ENGINE)
if _engine not in ENGINE_IDS:
    _engine = DEFAULT_ENGINE
_tess_cache: tuple[float, Optional[str], Optional[str]] = (0.0, None, None)   # (checked_at, exe, version)
_warned_missing = False


# ---------------------------------------------------------------------------
# Current choice
# ---------------------------------------------------------------------------

def get_engine() -> str:
    return _engine


def set_engine(name: str) -> None:
    global _engine
    if name not in ENGINE_IDS:
        raise ValueError(f"Unknown OCR engine: {name!r}")
    with _lock:
        _engine = name


# ---------------------------------------------------------------------------
# Tesseract discovery
# ---------------------------------------------------------------------------

def _candidates() -> list[str]:
    out: list[str] = []
    from backend import model_store       # DeepFind's own copy (Settings -> Models), no admin rights needed
    own = model_store.tess_dir() / ("tesseract.exe" if sys.platform.startswith("win") else "tesseract")
    if own.is_file():
        out.append(str(own))
    env = os.environ.get("DEEPFIND_TESSERACT")
    if env:
        out.append(env)
    found = shutil.which("tesseract")
    if found:
        out.append(found)
    conda = os.environ.get("CONDA_PREFIX")
    if conda:        # `conda install -c conda-forge tesseract`
        out += [os.path.join(conda, "Library", "bin", "tesseract.exe"), os.path.join(conda, "bin", "tesseract")]
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            if base:
                out.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
    return out


def _no_window_flags() -> int:
    return 0x08000000 if sys.platform.startswith("win") else 0     # CREATE_NO_WINDOW


def _probe(exe: str) -> Optional[str]:
    """Version string if `exe` is a working tesseract, else None."""
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=8,
                           creationflags=_no_window_flags())
        text = (p.stdout or p.stderr or "").strip()
        if p.returncode == 0 and text:
            return text.splitlines()[0].strip()
    except Exception:
        pass
    return None


def find_tesseract(refresh: bool = False) -> tuple[Optional[str], Optional[str]]:
    """(path, version) of a working Tesseract, or (None, None). Cached for 30 s."""
    global _tess_cache
    at, exe, ver = _tess_cache
    if not refresh and time.time() - at < 30:
        return exe, ver
    exe, ver = None, None
    for cand in _candidates():
        if cand and (os.path.isfile(cand) or shutil.which(cand)):
            v = _probe(cand)
            if v:
                exe, ver = cand, v
                break
    _tess_cache = (time.time(), exe, ver)
    return exe, ver


# ---------------------------------------------------------------------------
# Status for the UI
# ---------------------------------------------------------------------------

def engines_info(rapidocr_models_ok: bool, refresh: bool = False) -> list[dict]:
    """[{id,label,description,available,detail}] — `detail` is a short status line."""
    exe, ver = find_tesseract(refresh=refresh)
    info = []
    for e in ENGINES:
        item = dict(e)
        if e["id"] == "rapidocr":
            item["available"] = bool(rapidocr_models_ok)
            item["detail"] = "Ready" if rapidocr_models_ok else "Models not downloaded yet"
        elif e["id"] == "tesseract":
            item["available"] = bool(exe)
            item["detail"] = ver if exe else TESSERACT_HELP
            item["installable"] = (not exe) and can_auto_install()
            item["install_hint"] = "" if exe else manual_install_hint()
        else:
            item["available"] = True
            item["detail"] = "Always available"
        info.append(item)
    return info


def is_available(engine: str, rapidocr_models_ok: bool = True) -> bool:
    if engine == "tesseract":
        return find_tesseract()[0] is not None
    if engine == "rapidocr":
        return bool(rapidocr_models_ok)
    return engine == "off"


# ---------------------------------------------------------------------------
# Running Tesseract (called by vision_service)
# ---------------------------------------------------------------------------

def run_tesseract(img_bgr) -> str:
    """OCR a BGR numpy array with the Tesseract program and return the text."""
    global _warned_missing
    exe, _ = find_tesseract()
    if exe is None:
        if not _warned_missing:
            logger.warning(TESSERACT_HELP)
            _warned_missing = True
        return ""
    from PIL import Image          # lazy: keeps this module cheap to import

    from backend import model_store
    lang = os.environ.get("DEEPFIND_TESSERACT_LANG") or model_store.tesseract_langs()   # e.g. "eng+hin"
    env = dict(os.environ)
    td = model_store.tessdata_dir()
    if any((td / f"{c}.traineddata").is_file() for c in lang.split("+")):
        env["TESSDATA_PREFIX"] = str(td)          # language packs downloaded in Settings -> Models
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        Image.fromarray(img_bgr[:, :, ::-1]).save(tmp, format="PNG")
        p = subprocess.run([exe, tmp, "stdout", "-l", lang, "--psm", "3"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, creationflags=_no_window_flags(), env=env)
        if p.returncode != 0:
            logger.warning("tesseract failed (%s): %s", p.returncode, (p.stderr or "").strip()[:200])
            return ""
        return "\n".join(line.strip() for line in p.stdout.splitlines() if line.strip())
    finally:
        try:
            Path(tmp).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Installing Tesseract from inside the app / scripts/download_models.py
# ---------------------------------------------------------------------------
# Tesseract is a program, not a model file, so it can't be fetched like the ONNX models.
# On Windows we install it the same way a person would: winget if present, otherwise the
# official installer, run silently. Windows asks for permission (UAC) once — click Yes.

TESSERACT_INSTALLER_URL = ("https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/"
                           "tesseract-ocr-w64-setup-5.5.3.20260724.exe")
WINGET_ID = "UB-Mannheim.TesseractOCR"


def manual_install_hint() -> str:
    if sys.platform.startswith("win"):
        return "Install it from github.com/UB-Mannheim/tesseract/wiki, then press Check again."
    if sys.platform == "darwin":
        return "Run `brew install tesseract` in Terminal, then press Check again."
    return "Run `sudo apt install tesseract-ocr` (or your distro's equivalent), then press Check again."


def can_auto_install() -> bool:
    return sys.platform.startswith("win")


def _download(url: str, dest: Path, progress: Callable[[str], None]) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "DeepFind"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got, last = 0, 0.0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if time.time() - last > 0.4:
                last = time.time()
                progress(f"Downloading Tesseract… {got // 1_048_576} of {total // 1_048_576} MB" if total
                         else f"Downloading Tesseract… {got // 1_048_576} MB")
    os.replace(part, dest)


def install_tesseract(progress: Callable[[str], None] = lambda m: None) -> str:
    """Install Tesseract (Windows). Returns its version string; raises RuntimeError with a plain message."""
    if find_tesseract(refresh=True)[0]:
        return find_tesseract()[1] or "tesseract"
    if not can_auto_install():
        raise RuntimeError("Automatic install is only available on Windows. " + manual_install_hint())

    errors: list[str] = []
    if shutil.which("winget"):
        progress("Installing Tesseract with winget… accept the Windows prompt if one appears")
        try:
            p = subprocess.run(
                ["winget", "install", "-e", "--id", WINGET_ID, "--silent",
                 "--accept-package-agreements", "--accept-source-agreements"],
                capture_output=True, text=True, timeout=900, creationflags=_no_window_flags())
            if find_tesseract(refresh=True)[0]:
                return find_tesseract()[1] or "tesseract"
            errors.append(f"winget finished with code {p.returncode}")
        except Exception as e:
            errors.append(f"winget: {e}")

    installer = Path(tempfile.gettempdir()) / "deepfind-tesseract-setup.exe"
    try:
        _download(TESSERACT_INSTALLER_URL, installer, progress)
        progress("Installing Tesseract… accept the Windows prompt if one appears")
        if sys.platform.startswith("win"):
            # Use PowerShell to elevate the process (UAC prompt) because a direct subprocess.run fails with WinError 740
            subprocess.run(["powershell", "-NoProfile", "-Command", 
                            f"Start-Process -FilePath '{installer}' -ArgumentList '/S' -Verb RunAs -Wait"], timeout=900)
        else:
            subprocess.run([str(installer), "/S"], timeout=900)
    except Exception as e:
        errors.append(str(e))
    finally:
        try:
            installer.unlink()
        except OSError:
            pass
    found = find_tesseract(refresh=True)
    if found[0]:
        return found[1] or "tesseract"
    raise RuntimeError("Tesseract could not be installed automatically"
                       + (f" ({errors[-1]})" if errors else "") + ". " + manual_install_hint())


class TesseractInstaller:
    """Background install job, polled by the UI like the model download."""

    def __init__(self):
        self.state, self.message = "idle", ""          # idle | running | done | failed
        self._lock = threading.Lock()
        self._on_done: Optional[Callable[[], None]] = None

    def info(self) -> dict:
        return {"state": self.state, "message": self.message}

    def start(self, on_done: Optional[Callable[[], None]] = None) -> dict:
        with self._lock:
            if self.state == "running":
                return self.info()
            self.state, self.message, self._on_done = "running", "Starting…", on_done
        threading.Thread(target=self._run, daemon=True, name="tesseract-install").start()
        return self.info()

    def _run(self):
        try:
            ver = install_tesseract(lambda m: setattr(self, "message", m))
            self.state, self.message = "done", f"Installed ({ver})"
            if self._on_done:
                self._on_done()
        except Exception as e:
            logger.exception("tesseract install failed")
            self.state, self.message = "failed", str(e)


tesseract_installer = TesseractInstaller()