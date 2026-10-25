"""
backend/models.py — AI model status + one-click download for the desktop app.

The installer ships without the ~350 MB of models. The UI shows which are present and
can fetch them with scripts/download_models.py, run in a SUBPROCESS so that:
  * the running engine keeps HF_HUB_OFFLINE=1 (never touches the network on its own);
  * a crash/timeout in the downloader can't take the search engine down.
When the download finishes, the engines are told to pick the models up (no restart).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("deepfind.models")

ROOT = Path(__file__).resolve().parent.parent
STEPS = ["en_PP-OCRv3_det_infer.onnx", "en_PP-OCRv3_rec_infer.onnx", "en_dict.txt",
         "clip-ViT-B-32-vision", "clip-ViT-B-32-text"]


def clip_dir() -> Path:
    return Path(os.environ.get("DEEPFIND_CLIP_CACHE_DIR", "./models/clip_cache"))


def ocr_dir() -> Path:
    return Path(os.environ.get("DEEPFIND_OCR_MODELS_DIR", "./models/ocr_en"))


def models_root() -> Path:
    return Path(os.environ.get("DEEPFIND_MODELS_DIR", str(clip_dir().parent)))


def _has_onnx(fragment: str) -> bool:
    d = clip_dir()
    if not d.is_dir():
        return False
    for sub in d.iterdir():
        if fragment in sub.name and any(sub.rglob("*.onnx")):
            return True
    return False


def parts() -> dict:
    """Are the models chosen in Settings -> Models on disk? (text/vision = the image-search model,
    ocr = the chosen RapidOCR set). Read by the status badges in the Search/Activity views."""
    from backend import model_store
    spec = model_store.image_spec()
    return {"text": bool(spec) and model_store._clip_has(spec["text_repo"], spec["text_file"]),
            "vision": bool(spec) and model_store._clip_has(spec["image_repo"], spec["image_file"]),
            "ocr": model_store.rapidocr_files() is not None}


def _python_exe() -> str:
    """A console-less interpreter that can run scripts. Inside the Windows build the
    process is DeepFind.exe (embedded Python), so sys.executable is NOT python."""
    override = os.environ.get("DEEPFIND_PYTHON")
    if override and Path(override).exists():
        return override
    base = Path(sys.prefix)
    for name in ("pythonw.exe", "python.exe", "bin/python3", "bin/python"):
        if (base / name).exists():
            return str(base / name)
    return sys.executable


class Downloader:
    def __init__(self):
        self.state = "idle"          # idle | running | done | failed
        self.message = ""
        self.step = 0
        self._lock = threading.Lock()
        self._on_done: Optional[Callable[[], None]] = None

    def info(self) -> dict:
        p = parts()
        from backend import model_store
        a = model_store.active()
        need = ([p["text"]] if a["image_model"] != "off" else []) + \
               ([p["ocr"]] if a["ocr_engine"] == "rapidocr" else [])
        return {"root": str(models_root().resolve()), "installed": all(need), "parts": p,
                "job": {"state": self.state, "message": self.message, "step": self.step, "steps": len(STEPS)}}

    def start(self, on_done: Optional[Callable[[], None]] = None) -> dict:
        with self._lock:
            if self.state == "running":
                return self.info()
            self.state, self.message, self.step = "running", "Starting download…", 0
            self._on_done = on_done
        threading.Thread(target=self._run, daemon=True, name="model-download").start()
        return self.info()

    def _run(self):
        env = dict(os.environ)
        env.pop("HF_HUB_OFFLINE", None)
        env.pop("TRANSFORMERS_OFFLINE", None)
        env["PYTHONUNBUFFERED"] = "1"
        env["DEEPFIND_CLIP_CACHE_DIR"] = str(clip_dir().resolve())
        env["DEEPFIND_OCR_MODELS_DIR"] = str(ocr_dir().resolve())
        flags = 0x08000000 if sys.platform.startswith("win") else 0   # CREATE_NO_WINDOW
        tail: list[str] = []
        try:
            proc = subprocess.Popen(
                [_python_exe(), str(ROOT / "scripts" / "download_models.py"), "--tesseract"],
                cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", creationflags=flags)
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                tail = (tail + [line])[-8:]
                for i, key in enumerate(STEPS):
                    if key in line and ("Downloading" in line or "[skip]" in line):
                        self.step = max(self.step, i)
                        self.message = f"Downloading {key.split('_infer')[0]}… ({i + 1}/{len(STEPS)})"
            code = proc.wait()
            if code != 0:
                raise RuntimeError(tail[-1] if tail else f"downloader exited with {code}")
            self.step = len(STEPS)
            self.state, self.message = "done", "Models installed"
            logger.info("models downloaded to %s", models_root())
            if self._on_done:
                self._on_done()
        except Exception as e:
            logger.exception("model download failed")
            text = str(e)
            if any(k in text for k in ("URLError", "resolve", "Connection", "timed out", "403", "SSL")):
                text = "Couldn't reach the model server. Check your internet connection and try again."
            self.state, self.message = "failed", f"Download failed: {text}"


downloader = Downloader()
