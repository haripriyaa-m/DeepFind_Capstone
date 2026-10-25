"""
backend/model_store.py — every model DeepFind can use, which ones are on this PC, and which are active.

Nothing is downloaded until the user ticks it in Settings -> Models and presses Download.
Each model serves one ROLE ("for what"):

  ocr     Text inside images & scanned PDFs   (RapidOCR model sets, the Tesseract engine,
                                                Tesseract language packs)
  image   Find photos by what they show,       (CLIP-style text+image model pairs; the text half
          and search by meaning                 encodes queries, the image half encodes pictures)

Users can add their own entries (Settings -> Models -> Add your own model):
  tess-lang  a Tesseract language pack by code (e.g. "hin") or by URL to a .traineddata file
  rapidocr   a RapidOCR/PaddleOCR model set: links to det .onnx, rec .onnx and (optional) the keys file
  clip       a CLIP-style model on Hugging Face (ONNX, fastembed layout): repo, text/image file, dimension

Layout under the models folder (%LOCALAPPDATA%\\DeepFind\\models in the app):
  ocr_en/                built-in RapidOCR English set (same place as before 0.3.1)
  ocr/<id>/              other RapidOCR sets
  tesseract/             private copy of the Tesseract program (Windows; no admin rights needed)
  tessdata/              Tesseract language packs (*.traineddata)
  clip_cache/            Hugging Face/fastembed cache for the image-search models
  custom_models.json     the user's own entries

The engine asks this module (cheaply, on every use) which files to load, so a change in Settings
takes effect without restarting.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("deepfind.model_store")

ROOT = Path(__file__).resolve().parent.parent
IS_WIN = sys.platform.startswith("win")

ROLES = [
    {"id": "ocr", "label": "Text inside images & scanned PDFs",
     "description": "Reads the words in photos, screenshots and scanned pages so you can search for them."},
    {"id": "image", "label": "Photos by what they show",
     "description": "Lets you find pictures by describing them (\"dog on a beach\") and powers search by meaning."},
]

# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------

def clip_dir() -> Path:
    return Path(os.environ.get("DEEPFIND_CLIP_CACHE_DIR", "./models/clip_cache"))


def ocr_en_dir() -> Path:
    return Path(os.environ.get("DEEPFIND_OCR_MODELS_DIR", "./models/ocr_en"))


def root() -> Path:
    return Path(os.environ.get("DEEPFIND_MODELS_DIR", str(clip_dir().parent)))


def tess_dir() -> Path:
    return root() / "tesseract"


def tessdata_dir() -> Path:
    return root() / "tessdata"


def custom_path() -> Path:
    return root() / "custom_models.json"


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

_HF = "https://huggingface.co/SWHL/RapidOCR/resolve/main"
TESSDATA_FAST = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/{code}.traineddata"
TESSERACT_SETUP_URL = ("https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/"
                       "tesseract-ocr-w64-setup-5.5.3.20260724.exe")
SEVENZIP_R_URL = "https://github.com/ip7z/7zip/releases/download/24.09/7zr.exe"        # official 7-Zip builds
SEVENZIP_X64_URL = "https://github.com/ip7z/7zip/releases/download/24.09/7z2409-x64.exe"

# speed / accuracy are 1-5 for the comparison bars in the UI
BUILTIN: list[dict] = [
    {"id": "rapidocr-en", "role": "ocr", "kind": "rapidocr", "name": "RapidOCR · English",
     "size_mb": 10, "speed": 5, "accuracy": 3, "languages": "English",
     "summary": "Small and fast. The best default for screenshots, photos of documents and clean scans.",
     "good": "Fastest on any laptop · tiny download · no extra program",
     "weak": "English only · weaker on handwriting and unusual fonts",
     "files": {"det": f"{_HF}/PP-OCRv4/en_PP-OCRv3_det_infer.onnx",
               "rec": f"{_HF}/PP-OCRv3/en_PP-OCRv3_rec_infer.onnx",
               "keys": "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/release/2.7/ppocr/utils/en_dict.txt"}},
    {"id": "tesseract", "role": "ocr", "kind": "tesseract", "name": "Tesseract 5 · engine + English",
     "size_mb": 30, "speed": 3, "accuracy": 4, "languages": "English (add more with language packs)",
     "summary": "The classic open-source OCR engine. Strong on printed pages, books and multi-column scans, "
                "and reads 100+ languages through language packs.",
     "good": "Many languages · very good on clean printed text and long documents",
     "weak": "Slower · weaker on photos and screenshots with busy backgrounds"},
    {"id": "clip-vit-b-32", "role": "image", "kind": "clip", "name": "CLIP ViT-B/32",
     "size_mb": 600, "speed": 4, "accuracy": 3, "languages": "English", "dim": 512,
     "text_repo": "Qdrant/clip-ViT-B-32-text", "text_file": "model.onnx",
     "image_repo": "Qdrant/clip-ViT-B-32-vision", "image_file": "model.onnx",
     "summary": "The balanced default. Finds photos from short descriptions such as \"birthday cake\" or \"red car\".",
     "good": "Quick on any CPU · well tested",
     "weak": "Best with short queries · English"},
    {"id": "jina-clip-v1", "role": "image", "kind": "clip", "name": "Jina CLIP v1",
     "size_mb": 900, "speed": 2, "accuracy": 4, "languages": "English", "dim": 768,
     "text_repo": "jinaai/jina-clip-v1", "text_file": "onnx/text_model.onnx",
     "image_repo": "jinaai/jina-clip-v1", "image_file": "onnx/vision_model.onnx",
     "summary": "A newer, larger model that understands longer and more detailed descriptions.",
     "good": "More accurate matches · long queries (\"whiteboard photo with a timeline from the March meeting\")",
     "weak": "Bigger download · slower on older laptops"},
]

TESS_LANGS = [("eng", "English"), ("hin", "Hindi"), ("tam", "Tamil"), ("tel", "Telugu"), ("kan", "Kannada"),
              ("mal", "Malayalam"), ("ben", "Bengali"), ("mar", "Marathi"), ("guj", "Gujarati"),
              ("pan", "Punjabi"), ("urd", "Urdu"), ("san", "Sanskrit"), ("ara", "Arabic"),
              ("chi_sim", "Chinese (Simplified)"), ("jpn", "Japanese"), ("kor", "Korean"),
              ("fra", "French"), ("deu", "German"), ("spa", "Spanish")]
for _code, _name in TESS_LANGS:
    BUILTIN.append({
        "id": f"tess-{_code}", "role": "ocr", "kind": "tess-lang", "name": f"{_name} language pack",
        "code": _code, "size_mb": 2 if _code == "eng" else 5, "requires": "tesseract", "languages": _name,
        "summary": f"Lets Tesseract read {_name}." + (" Installed together with the engine." if _code == "eng" else ""),
        "good": "Small · add as many as you need", "weak": "Needs the Tesseract engine"})

_CODE_RE = re.compile(r"^[A-Za-z_]{3,24}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FILE_RE = re.compile(r"^[A-Za-z0-9_./-]+\.onnx$")


def _load_custom() -> list[dict]:
    try:
        items = json.loads(custom_path().read_text(encoding="utf-8"))
        return [dict(i, custom=True) for i in items if isinstance(i, dict) and i.get("id")]
    except (OSError, ValueError):
        return []


def _save_custom(items: list[dict]) -> None:
    custom_path().parent.mkdir(parents=True, exist_ok=True)
    clean = [{k: v for k, v in i.items() if k != "custom"} for i in items]
    custom_path().write_text(json.dumps(clean, indent=2), encoding="utf-8")


def all_items() -> list[dict]:
    return [dict(i, custom=False) for i in BUILTIN] + _load_custom()


def get(item_id: str) -> Optional[dict]:
    return next((i for i in all_items() if i["id"] == item_id), None)


def _https(url: str, what: str) -> str:
    url = (url or "").strip()
    if not url.startswith("https://"):
        raise ValueError(f"{what}: enter an https:// link")
    return url


def add_custom(body: dict) -> dict:
    """Validate and store a user-defined model. Returns the new entry."""
    kind = body.get("kind")
    name = (body.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("Give the model a name")
    item: dict = {"id": f"custom-{uuid.uuid4().hex[:8]}", "kind": kind, "name": name,
                  "summary": (body.get("note") or "").strip()[:200] or "Added by you.",
                  "good": "", "weak": "", "speed": 0, "accuracy": 0, "size_mb": 0}
    if kind == "tess-lang":
        code = (body.get("code") or "").strip()
        if not _CODE_RE.match(code):
            raise ValueError("Language code: 3+ letters as Tesseract names them, e.g. hin, tam, chi_tra")
        item.update(role="ocr", code=code, requires="tesseract", languages=name,
                    url=_https(body["url"], "Download link") if body.get("url") else TESSDATA_FAST.format(code=code))
    elif kind == "rapidocr":
        item.update(role="ocr", languages=(body.get("languages") or "").strip()[:60] or "—",
                    files={"det": _https(body.get("det_url", ""), "Detection model link"),
                           "rec": _https(body.get("rec_url", ""), "Recognition model link"),
                           **({"keys": _https(body["keys_url"], "Keys/dictionary link")} if body.get("keys_url") else {})})
    elif kind == "clip":
        repo = (body.get("repo") or "").strip()
        if not _REPO_RE.match(repo):
            raise ValueError("Hugging Face repository: owner/name, e.g. jinaai/jina-clip-v1")
        tf = (body.get("text_file") or "onnx/text_model.onnx").strip()
        vf = (body.get("image_file") or "onnx/vision_model.onnx").strip()
        if not (_FILE_RE.match(tf) and _FILE_RE.match(vf)):
            raise ValueError("Model files must be .onnx paths inside the repository")
        try:
            dim = int(body.get("dim") or 0)
        except (TypeError, ValueError):
            dim = 0
        if not 32 <= dim <= 4096:
            raise ValueError("Vector size (dimension) must be a number such as 512 or 768")
        item.update(role="image", languages=(body.get("languages") or "").strip()[:60] or "—", dim=dim,
                    text_repo=repo, text_file=tf, image_repo=(body.get("image_repo") or repo).strip(), image_file=vf)
        if not _REPO_RE.match(item["image_repo"]):
            raise ValueError("Image repository: owner/name")
    else:
        raise ValueError("Unknown model type")
    items = _load_custom()
    items.append(item)
    _save_custom(items)
    return dict(item, custom=True)


def delete_custom(item_id: str) -> None:
    item = get(item_id)
    if not item or not item.get("custom"):
        raise ValueError("Only models you added yourself can be deleted")
    if is_active(item_id):
        raise ValueError("This model is in use. Choose another one first.")
    remove_files(item)
    _save_custom([i for i in _load_custom() if i["id"] != item_id])


# ---------------------------------------------------------------------------
# which files belong to an item, and is it installed?
# ---------------------------------------------------------------------------

def _rapid_dir(item: dict) -> Path:
    return ocr_en_dir() if item["id"] == "rapidocr-en" else root() / "ocr" / item["id"]


def rapid_paths(item: dict) -> dict:
    d = _rapid_dir(item)
    if item["id"] == "rapidocr-en":           # names used since 0.2 (keeps existing downloads valid)
        return {"det": d / "en_PP-OCRv3_det_infer.onnx", "rec": d / "en_PP-OCRv3_rec_infer.onnx", "keys": d / "en_dict.txt"}
    return {k: d / f"{k}{'.txt' if k == 'keys' else '.onnx'}" for k in item.get("files", {})}


def tesseract_exe() -> Optional[str]:
    """Our private copy first, then a system-wide install (PATH / usual folders)."""
    from backend import ocr_engines
    return ocr_engines.find_tesseract()[0]


def _clip_has(repo: str, file: str) -> bool:
    d = clip_dir()
    if not d.is_dir():
        return False
    tail = repo.split("/")[-1].lower()
    base = file.rsplit("/", 1)[-1]
    for sub in d.iterdir():
        if sub.is_dir() and tail in sub.name.lower():
            if any(p.name == base and p.as_posix().endswith(file) for p in sub.rglob(base)):
                return True
    return False


def is_installed(item: dict) -> bool:
    kind = item["kind"]
    if kind == "rapidocr":
        p = rapid_paths(item)
        return p["det"].is_file() and p["rec"].is_file()
    if kind == "tesseract":
        return tesseract_exe() is not None
    if kind == "tess-lang":
        return (tessdata_dir() / f"{item['code']}.traineddata").is_file()
    if kind == "clip":
        return _clip_has(item["text_repo"], item["text_file"]) and _clip_has(item["image_repo"], item["image_file"])
    return False


def remove_files(item: dict) -> None:
    kind = item["kind"]
    if kind == "rapidocr":
        if item["id"] == "rapidocr-en":
            for p in rapid_paths(item).values():
                Path(p).unlink(missing_ok=True)
        else:
            shutil.rmtree(_rapid_dir(item), ignore_errors=True)
    elif kind == "tesseract":
        if not tess_dir().is_dir():
            raise ValueError("Tesseract was installed outside DeepFind; remove it from Windows Settings > Apps.")
        shutil.rmtree(tess_dir(), ignore_errors=True)
    elif kind == "tess-lang":
        (tessdata_dir() / f"{item['code']}.traineddata").unlink(missing_ok=True)
    elif kind == "clip":
        others = [i for i in all_items() if i["kind"] == "clip" and i["id"] != item["id"] and is_installed(i)]
        shared = {r for o in others for r in (o["text_repo"], o["image_repo"])}
        for repo in {item["text_repo"], item["image_repo"]} - shared:
            tail = repo.split("/")[-1].lower()
            if clip_dir().is_dir():
                for sub in clip_dir().iterdir():
                    if sub.is_dir() and tail in sub.name.lower():
                        shutil.rmtree(sub, ignore_errors=True)


# ---------------------------------------------------------------------------
# active choice (pushed in from settings by backend/main.py)
# ---------------------------------------------------------------------------

DEFAULT_ACTIVE = {"ocr_engine": "rapidocr", "ocr_model": "rapidocr-en", "ocr_languages": ["eng"],
                  "image_model": "clip-vit-b-32"}
_active = dict(DEFAULT_ACTIVE)
_active_lock = threading.Lock()


def set_active(**kw) -> None:
    with _active_lock:
        for k, v in kw.items():
            if k in _active and v is not None:
                _active[k] = list(v) if isinstance(v, (list, tuple)) else v


def active() -> dict:
    with _active_lock:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _active.items()}


def is_active(item_id: str) -> bool:
    a = active()
    item = get(item_id) or {}
    if item.get("kind") == "tess-lang":
        return a["ocr_engine"] == "tesseract" and item.get("code") in a["ocr_languages"]
    if item.get("kind") == "tesseract":
        return a["ocr_engine"] == "tesseract"
    return item_id in (a["image_model"], a["ocr_model"] if a["ocr_engine"] == "rapidocr" else None)


def rapidocr_files() -> Optional[dict]:
    """det/rec/keys paths of the RapidOCR set in use (None if it isn't downloaded)."""
    item = get(active()["ocr_model"]) or get("rapidocr-en")
    if not item or not is_installed(item):
        return None
    p = rapid_paths(item)
    return {"id": item["id"], "det": str(p["det"]), "rec": str(p["rec"]),
            "keys": str(p["keys"]) if "keys" in p and Path(p["keys"]).is_file() else None}


def tesseract_langs() -> str:
    """'eng+hin' — only the packs that are actually on disk."""
    langs = [c for c in active()["ocr_languages"] if (tessdata_dir() / f"{c}.traineddata").is_file()]
    return "+".join(langs) or "eng"


def image_spec() -> Optional[dict]:
    """The image-search model in use, or None when it is switched off."""
    mid = active()["image_model"]
    if mid == "off":
        return None
    item = get(mid) or get("clip-vit-b-32")
    return {k: item[k] for k in ("id", "dim", "text_repo", "text_file", "image_repo", "image_file", "custom")}


def register_fastembed(spec: dict) -> None:
    """Teach fastembed about a custom (user-added) CLIP model before it is loaded."""
    if not spec.get("custom"):
        return
    from fastembed import TextEmbedding
    from fastembed.common.model_description import DenseModelDescription, ModelSource, PoolingType
    from fastembed.image import onnx_embedding as img
    if not any(m.model.lower() == spec["text_repo"].lower() for m in TextEmbedding._list_supported_models()):
        TextEmbedding.add_custom_model(model=spec["text_repo"], pooling=PoolingType.DISABLED, normalization=True,
                                       sources=ModelSource(hf=spec["text_repo"]), dim=spec["dim"],
                                       model_file=spec["text_file"], description="custom (DeepFind)")
    if not any(m.model.lower() == spec["image_repo"].lower() for m in img.supported_onnx_models):
        img.supported_onnx_models.append(DenseModelDescription(
            model=spec["image_repo"], sources=ModelSource(hf=spec["image_repo"]), model_file=spec["image_file"],
            description="custom (DeepFind)", license="", size_in_GB=0.0, dim=spec["dim"]))


# ---------------------------------------------------------------------------
# downloads: one queue, one item at a time
# ---------------------------------------------------------------------------

def _python_exe() -> str:
    from backend.models import _python_exe as py
    return py()


def _fetch(url: str, dest: Path, report: Callable[[int, int], None]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "DeepFind"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as out:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            report(done, total)
    os.replace(part, dest)


class Jobs:
    def __init__(self):
        self._lock = threading.Lock()
        self.queue: list[str] = []
        self.current: Optional[str] = None
        self.message = ""
        self.done = 0
        self.total = 0
        self.errors: dict[str, str] = {}
        self._thread: Optional[threading.Thread] = None
        self.on_installed: Callable[[dict], None] = lambda item: None

    def info(self) -> dict:
        with self._lock:
            return {"queue": list(self.queue), "current": self.current, "message": self.message,
                    "progress": {"done": self.done, "total": self.total}, "errors": dict(self.errors)}

    def enqueue(self, ids: list[str]) -> None:
        with self._lock:
            for i in ids:
                item = get(i)
                if not item:
                    raise ValueError(f"Unknown model: {i}")
                if i != self.current and i not in self.queue and not is_installed(item):
                    if item.get("requires") and not is_installed(get(item["requires"]) or {"kind": "?"}) \
                            and item["requires"] not in self.queue and item["requires"] != self.current:
                        self.queue.append(item["requires"])        # language pack -> engine first
                    self.queue.append(i)
                    self.errors.pop(i, None)
            if self.queue and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, daemon=True, name="model-jobs")
                self._thread.start()

    def cancel(self, item_id: str) -> None:
        with self._lock:
            if item_id in self.queue:
                self.queue.remove(item_id)

    def _report(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    def _run(self):
        while True:
            with self._lock:
                if not self.queue:
                    self.current, self.message = None, ""
                    return
                self.current = self.queue.pop(0)
                self.done = self.total = 0
            item = get(self.current)
            try:
                self.message = f"Downloading {item['name']}…"
                install(item, self._report, lambda m: setattr(self, "message", m))
                if not is_installed(item):
                    raise RuntimeError("the files are incomplete after the download")
                logger.info("model installed: %s", item["id"])
                try:
                    self.on_installed(item)
                except Exception:
                    logger.exception("on_installed hook failed")
            except Exception as e:
                logger.exception("model download failed: %s", item["id"])
                self.errors[item["id"]] = _friendly(e)


def _friendly(e: Exception) -> str:
    t = f"{e}"
    if any(k in t for k in ("URLError", "getaddrinfo", "resolve", "timed out", "Connection", "SSL")):
        return "Couldn't reach the download server. Check the internet connection and try again."
    if "404" in t:
        return "The file wasn't found at that address (404). Check the link / name."
    return t[:300] or type(e).__name__


def install(item: dict, report: Callable[[int, int], None], say: Callable[[str], None]) -> None:
    kind = item["kind"]
    if kind == "rapidocr":
        paths = rapid_paths(item)
        for k, url in item["files"].items():
            if not paths[k].is_file():
                say(f"Downloading {item['name']} ({k})…")
                _fetch(url, paths[k], report)
    elif kind == "tess-lang":
        say(f"Downloading {item['name']}…")
        _fetch(item.get("url") or TESSDATA_FAST.format(code=item["code"]),
               tessdata_dir() / f"{item['code']}.traineddata", report)
    elif kind == "tesseract":
        _install_tesseract(report, say)
    elif kind == "clip":
        say(f"Downloading {item['name']} (this one is large — a few minutes)…")
        _run_fetch_script({k: item[k] for k in ("text_repo", "text_file", "image_repo", "image_file", "dim")}
                          | {"custom": bool(item.get("custom"))})
    else:
        raise RuntimeError(f"Don't know how to install {kind}")


def _install_tesseract(report, say) -> None:
    """Windows: unpack the official Tesseract installer into models\\tesseract WITHOUT running it,
    so no administrator rights or Windows prompt are needed. The installer is an NSIS archive;
    7-Zip (official build, fetched for this step only) reads it. Then add the English pack."""
    if not IS_WIN:
        if tesseract_exe():
            return
        raise RuntimeError("Install Tesseract with your package manager (e.g. sudo apt install tesseract-ocr), "
                           "then press Refresh.")
    work = Path(tempfile.mkdtemp(prefix="deepfind-tess-"))
    flags = 0x08000000                                  # CREATE_NO_WINDOW
    try:
        say("Downloading Tesseract (1/3)…")
        _fetch(TESSERACT_SETUP_URL, work / "setup.exe", report)
        say("Preparing the unpacker (2/3)…")
        _fetch(SEVENZIP_R_URL, work / "7zr.exe", report)
        _fetch(SEVENZIP_X64_URL, work / "7z-x64.exe", report)
        subprocess.run([str(work / "7zr.exe"), "x", "-y", f"-o{work / '7z'}", str(work / "7z-x64.exe"), "7z.exe", "7z.dll"],
                       check=True, capture_output=True, timeout=300, creationflags=flags)
        say("Unpacking Tesseract (3/3)…")
        tmp = tess_dir().with_name("tesseract.new")
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run([str(work / "7z" / "7z.exe"), "x", "-y", "-tnsis", f"-o{tmp}", str(work / "setup.exe"),
                        "tesseract.exe", "*.dll"], check=True, capture_output=True, timeout=600, creationflags=flags)
        exe = tmp / "tesseract.exe"
        if not exe.is_file():
            raise RuntimeError("tesseract.exe was not found in the installer")
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, creationflags=flags)
        if out.returncode != 0:
            raise RuntimeError(f"Tesseract doesn't start: {(out.stderr or out.stdout).strip()[:200]}")
        shutil.rmtree(tess_dir(), ignore_errors=True)
        os.replace(tmp, tess_dir())
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not (tessdata_dir() / "eng.traineddata").is_file():
        say("Downloading the English language pack…")
        _fetch(TESSDATA_FAST.format(code="eng"), tessdata_dir() / "eng.traineddata", report)
    from backend import ocr_engines
    ocr_engines.find_tesseract(refresh=True)


def _run_fetch_script(spec: dict) -> None:
    """fastembed/huggingface_hub read HF_HUB_OFFLINE when they are imported, and the running app
    keeps it on, so CLIP downloads run in a child process that is allowed online."""
    env = dict(os.environ)
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)
    env["PYTHONUNBUFFERED"] = "1"
    env["DEEPFIND_CLIP_CACHE_DIR"] = str(clip_dir().resolve())
    flags = 0x08000000 if IS_WIN else 0
    p = subprocess.run([_python_exe(), str(ROOT / "scripts" / "fetch_model.py"), json.dumps(spec)],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600, creationflags=flags)
    if p.returncode != 0:
        lines = [l for l in (p.stdout + p.stderr).strip().splitlines() if l.strip()]
        raise RuntimeError(lines[-1] if lines else f"download exited with {p.returncode}")


jobs = Jobs()


# ---------------------------------------------------------------------------
# what the Settings page shows
# ---------------------------------------------------------------------------

def catalog() -> dict:
    j = jobs.info()
    items = []
    for it in all_items():
        st = ("downloading" if j["current"] == it["id"] else "queued" if it["id"] in j["queue"]
              else "installed" if is_installed(it) else "failed" if it["id"] in j["errors"] else "available")
        items.append({k: v for k, v in it.items() if k != "files"} | {
            "status": st, "error": j["errors"].get(it["id"], ""), "active": is_active(it["id"])})
    return {"roles": ROLES, "items": items, "active": active(), "jobs": j,
            "tesseract_path": tesseract_exe() or "", "models_dir": str(root().resolve())}
