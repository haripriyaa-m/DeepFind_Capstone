"""
backend/main.py — Karthik's FastAPI gateway (localhost only).

Endpoints:
  GET  /api/health
  GET  /api/search?q=...&limit=20
  GET  /api/status                  indexing progress, queues, CPU/RAM
  GET  /api/settings   POST /api/settings
  POST /api/reindex                 forget hashes and rescan watched folders (full rebuild)
  POST /api/index-now               read everything that changed since the last run
  POST /api/reveal                  open the file in Explorer / Finder / file manager
  POST /api/open                    open the file with its default app          (desktop UI)
  GET  /api/thumb?path=&size=       JPEG preview of an indexed image / PDF page 1 / video frame at &t= seconds
  GET  /api/library                 {file_type: n} + {folder: n} breakdown of the index (desktop UI)
  GET  /api/models   POST /api/models/download    AI model status / one-click download (desktop UI);
                                    GET also lists the selectable OCR engines
  GET  /api/update   POST /api/update/{check,download,install,repo,token}    in-app updates (backend/updater.py)
  GET  /                                the built desktop UI (frontend/dist), when present

Run standalone (the desktop app embeds it via desktop/main.py):
    python backend/main.py --port 0        # prints DEEPFIND_PORT=<n> on stdout
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import socket
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _default_model_dirs():
    """Point the engines at the models folder BEFORE they are imported (they read env at import).
    Priority: explicit DEEPFIND_*_DIR env > DEEPFIND_MODELS_DIR > models/ next to the frozen exe > ./models"""
    root = os.environ.get("DEEPFIND_MODELS_DIR")
    if not root and getattr(sys, "frozen", False):
        cand = Path(sys.executable).resolve().parent / "models"
        if cand.is_dir():
            root = str(cand)
    if root:
        os.environ.setdefault("DEEPFIND_CLIP_CACHE_DIR", str(Path(root) / "clip_cache"))
        os.environ.setdefault("DEEPFIND_OCR_MODELS_DIR", str(Path(root) / "ocr_en"))


_default_model_dirs()

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import ingestion_service as isvc
import search_engine as se
from backend import model_store, ocr_engines
from backend.models import downloader, parts as model_parts
from backend.updater import UpdateError, read_version, updater
from pipeline import pipeline

logger = logging.getLogger("deepfind.api")

API_VERSION = read_version()      # the VERSION file — one place to bump a release

DEFAULT_SETTINGS = {
    "watched_folders": [], "cpu_limit": 50, "ram_limit": 75, "pause_on_battery": True,
    "index_schedule": "realtime",      # realtime | 15m | 30m | 1h | 6h | 24h | manual
    "ocr_engine": "rapidocr",          # rapidocr | tesseract | off
    "last_index_run": 0,               # epoch seconds, written by the scheduler
    "auto_update_check": True,         # look for a new version once a day (one request to github.com)
    "ocr_model": "rapidocr-en",        # RapidOCR model set (Settings -> Models)
    "ocr_languages": ["eng"],          # Tesseract language packs in use
    "image_model": "clip-vit-b-32",    # image-search model, or "off"
}


def _push_active(s: dict) -> None:
    model_store.set_active(ocr_engine=s["ocr_engine"], ocr_model=s["ocr_model"],
                           ocr_languages=s["ocr_languages"], image_model=s["image_model"])


def _data_dir() -> Path:
    return Path(os.environ.get("DEEPFIND_DATA_DIR", Path.home() / ".local_semantic_search"))


def _settings_path() -> Path:
    return _data_dir() / "settings.json"


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(_settings_path().read_text()))
    except Exception:
        pass
    return s


def save_settings(s: dict):
    _data_dir().mkdir(parents=True, exist_ok=True)
    _settings_path().write_text(json.dumps(s, indent=2))


class SettingsIn(BaseModel):
    watched_folders: Optional[list[str]] = None
    cpu_limit: Optional[int] = Field(None, ge=5, le=100)
    ram_limit: Optional[int] = Field(None, ge=10, le=100)
    pause_on_battery: Optional[bool] = None
    index_schedule: Optional[Literal["realtime", "15m", "30m", "1h", "6h", "24h", "manual"]] = None
    ocr_engine: Optional[Literal["rapidocr", "tesseract", "off"]] = None
    auto_update_check: Optional[bool] = None


class RevealIn(BaseModel):
    file_path: str


_ready = threading.Event()
_boot_error: Optional[str] = None


def _remember_index_run(ts: float):
    s = load_settings()
    s["last_index_run"] = ts
    save_settings(s)


def _boot():
    """Runs in a thread so the HTTP server (and the UI's health polling) is up immediately."""
    global _boot_error
    try:
        s = load_settings()
        _push_active(s)                       # which models to load comes from Settings -> Models
        model_store.jobs.on_installed = _model_installed
        db_path = os.environ.get("DEEPFIND_DB_PATH", str(_data_dir() / "lancedb"))
        se.init_db(db_path, preload_encoder=False)
        ocr_engines.set_engine(s["ocr_engine"] if s["ocr_engine"] in ocr_engines.ENGINE_IDS else "rapidocr")
        pipeline.on_index_run = _remember_index_run
        pipeline.start(s["watched_folders"], s["cpu_limit"], s["ram_limit"], s["pause_on_battery"],
                       index_schedule=s["index_schedule"], last_index_run=s["last_index_run"])
        if se.last_init_rebuilt:             # image model's vector size changed -> read everything again
            pipeline.reindex()
        _ready.set()
        updater.clean_old()
        updater.start_background(lambda: bool(load_settings().get("auto_update_check", True)))
        se.warm_up()   # load the CLIP text model in the background (failure -> keyword-only)
    except Exception as e:
        logger.exception("boot failed")
        _boot_error = f"{type(e).__name__}: {e}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ready.clear()
    threading.Thread(target=_boot, daemon=True, name="boot").start()
    yield
    pipeline.stop()


app = FastAPI(title="DeepFind local API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # the desktop UI is same-origin; this is only for the Vite dev server
    allow_origins=["http://localhost:1420", "http://127.0.0.1:1420"],
    allow_methods=["*"], allow_headers=["*"],
)


def _require_ready():
    if _boot_error:
        raise HTTPException(500, _boot_error)
    if not _ready.is_set():
        raise HTTPException(503, "Engine is starting")


@app.get("/api/health")
def health():
    return {"ok": True, "ready": _ready.is_set(), "error": _boot_error, "version": API_VERSION}


@app.get("/api/search")
def api_search(q: str = Query("", max_length=500), limit: int = Query(20, ge=1, le=100)):
    _require_ready()
    if not q.strip():
        return {"query": q, "results": []}
    return {"query": q, "results": [r.to_dict() for r in se.search(q, limit=limit)]}


@app.get("/api/status")
def api_status():
    if not _ready.is_set():
        return {"ready": False, "error": _boot_error, "message": "Starting..."}
    s = pipeline.status()
    idle = not s["paused"] and s["tier2_queue"] == 0 and s["work_queue"] == 0
    s.update(se.semantic_status())
    s.update(ready=True, db_rows=se.count(), idle=idle,
             message=f"Indexed {s['indexed_files']:,} files | {s['tier2_queue']} in Tier 2 queue "
                     f"({'Idle' if idle else 'Paused' if s['paused'] else 'Working'})")
    return s


@app.get("/api/settings")
def get_settings():
    return load_settings()


@app.post("/api/settings")
def post_settings(body: SettingsIn):
    _require_ready()
    s = load_settings()
    patch = body.model_dump(exclude_none=True)
    engine = patch.get("ocr_engine")
    if engine and engine != s["ocr_engine"] and not ocr_engines.is_available(engine, model_parts()["ocr"]):
        raise HTTPException(400, ocr_engines.TESSERACT_HELP if engine == "tesseract"
                            else "The RapidOCR models are not downloaded yet. Download them under AI models first.")
    for k, v in patch.items():
        s[k] = v
    _push_active(s)
    invalid = [p for p in s["watched_folders"] if not os.path.isdir(p)]
    s["watched_folders"] = [os.path.normpath(os.path.abspath(p)) for p in s["watched_folders"]
                            if os.path.isdir(p)]
    save_settings(s)
    pipeline.apply_settings(s["watched_folders"], s["cpu_limit"], s["ram_limit"], s["pause_on_battery"],
                            index_schedule=s["index_schedule"], ocr_engine=s["ocr_engine"])
    return {**s, "invalid_folders": invalid}


@app.post("/api/reindex")
def reindex():
    _require_ready()
    pipeline.reindex()
    return {"ok": True}


@app.post("/api/index-now")
def index_now():
    """Read everything that changed since the last run (works in every schedule mode)."""
    _require_ready()
    pipeline.index_now()
    return {"ok": True}


def _reveal(path: str):
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def _open(path: str):
    if sys.platform.startswith("win"):
        os.startfile(os.path.normpath(path))          # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _indexed_existing(path: str) -> str:
    """Only files we indexed — never arbitrary paths from the client."""
    if not se.exists(path):
        raise HTTPException(404, "File is not in the index")
    if not os.path.exists(path):
        raise HTTPException(404, "File no longer exists on disk")
    return path


@app.post("/api/reveal")
def api_reveal(body: RevealIn):
    _require_ready()
    _reveal(_indexed_existing(body.file_path))
    return {"ok": True}


@app.post("/api/open")
def api_open(body: RevealIn):
    _require_ready()
    _open(_indexed_existing(body.file_path))
    return {"ok": True}


THUMB_IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}
THUMB_VIDEO_TYPES = {"mp4", "mkv", "mov", "avi", "webm", "m4v"}


@lru_cache(maxsize=256)
def _thumb_bytes(path: str, mtime: float, size: int, t: float = 0.0) -> bytes:
    from PIL import Image
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in THUMB_VIDEO_TYPES:                    # the frame at t seconds (the matched moment)
        import av
        import video_frames
        with av.open(path) as c:
            vs = c.streams.video[0]
            got = video_frames._frame_at(c, vs, max(0.0, t), float(vs.time_base))
            if got is None:
                raise ValueError("no frame")
            img = got[0].to_image().convert("RGB")
    elif ext == "pdf":
        import pymupdf
        with pymupdf.open(path) as doc:
            if doc.page_count == 0:
                raise ValueError("empty pdf")
            page = doc[0]
            zoom = max(0.1, min(2.0, size / max(page.rect.width, page.rect.height)))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    else:
        img = Image.open(path)
        img.draft("RGB", (size, size))              # fast JPEG downscale on decode
        img = img.convert("RGB")
    img.thumbnail((size, size))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return buf.getvalue()


@app.get("/api/thumb")
def api_thumb(path: str = Query(..., max_length=4096), size: int = Query(160, ge=32, le=640),
              t: float = Query(0.0, ge=0, le=1_000_000)):
    _require_ready()
    _indexed_existing(path)
    ext = Path(path).suffix.lower().lstrip(".")
    if ext not in THUMB_IMAGE_TYPES and ext not in THUMB_VIDEO_TYPES and ext != "pdf":
        raise HTTPException(415, "No preview for this file type")
    try:
        data = _thumb_bytes(path, os.path.getmtime(path), size, round(t, 1) if ext in THUMB_VIDEO_TYPES else 0.0)
    except Exception as e:
        raise HTTPException(422, f"Preview failed: {type(e).__name__}")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})


@app.get("/api/library")
def api_library():
    _require_ready()
    counts = se.type_counts()
    folders = {f: se.count_under(f) for f in load_settings()["watched_folders"]}
    return {"total": sum(counts.values()), "types": counts, "folders": folders}


def _models_info(refresh: bool = False) -> dict:
    """Model download status + the OCR engines the user can choose between."""
    info = downloader.info()
    info["tesseract_job"] = ocr_engines.tesseract_installer.info()
    info["ocr_engine"] = ocr_engines.get_engine()
    info["ocr_engines"] = ocr_engines.engines_info(info["parts"]["ocr"], refresh=refresh)
    return info


@app.get("/api/models")
def api_models(refresh: bool = False):
    """refresh=true re-checks for a Tesseract install (after the user installs it)."""
    return _models_info(refresh)


def _models_installed():
    se.reset_encoder_error()
    threading.Thread(target=se.warm_up, daemon=True, name="encoder-load").start()
    if _ready.is_set():
        pipeline.models_changed()


@app.post("/api/models/download")
def api_models_download():
    downloader.start(on_done=_models_installed)
    return _models_info()


# ---------------------------------------------------------------------------
# Settings -> Models: pick what to download, for what, and which one to use
# ---------------------------------------------------------------------------

def _model_installed(item: dict):
    """A download finished: let the engine pick the model up (images skipped earlier get read)."""
    ocr_engines.find_tesseract(refresh=True)
    se.reset_encoder_error()
    if item.get("role") == "image" and _ready.is_set():
        threading.Thread(target=se.warm_up, daemon=True, name="encoder-load").start()
    if _ready.is_set():
        pipeline.models_changed()


class IdsIn(BaseModel):
    ids: list[str] = []


class IdIn(BaseModel):
    id: str


class ActiveIn(BaseModel):
    ocr_engine: Optional[Literal["rapidocr", "tesseract", "off"]] = None
    ocr_model: Optional[str] = None
    ocr_languages: Optional[list[str]] = None
    image_model: Optional[str] = None


class CustomIn(BaseModel):
    kind: Literal["tess-lang", "rapidocr", "clip"]
    name: str
    note: str = ""
    languages: str = ""
    code: str = ""
    url: str = ""
    det_url: str = ""
    rec_url: str = ""
    keys_url: str = ""
    repo: str = ""
    image_repo: str = ""
    text_file: str = ""
    image_file: str = ""
    dim: int = 0


def _bad(e: Exception):
    raise HTTPException(400, str(e))


@app.get("/api/catalog")
def api_catalog():
    return model_store.catalog()


@app.post("/api/catalog/download")
def api_catalog_download(body: IdsIn):
    """Download exactly the ticked models (one after another). Nothing else is fetched."""
    try:
        model_store.jobs.enqueue(body.ids)
    except ValueError as e:
        _bad(e)
    return model_store.catalog()


@app.post("/api/catalog/cancel")
def api_catalog_cancel(body: IdIn):
    model_store.jobs.cancel(body.id)
    return model_store.catalog()


@app.post("/api/catalog/remove")
def api_catalog_remove(body: IdIn):
    item = model_store.get(body.id)
    if not item:
        raise HTTPException(404, "Unknown model")
    if model_store.is_active(body.id):
        raise HTTPException(400, "This model is in use. Choose another one first.")
    try:
        model_store.remove_files(item)
    except ValueError as e:
        _bad(e)
    ocr_engines.find_tesseract(refresh=True)
    return model_store.catalog()


@app.post("/api/catalog/custom")
def api_catalog_custom(body: CustomIn):
    try:
        model_store.add_custom(body.model_dump())
    except (ValueError, KeyError) as e:
        _bad(e)
    return model_store.catalog()


@app.post("/api/catalog/custom/delete")
def api_catalog_custom_delete(body: IdIn):
    try:
        model_store.delete_custom(body.id)
    except ValueError as e:
        _bad(e)
    return model_store.catalog()


@app.post("/api/catalog/active")
def api_catalog_active(body: ActiveIn):
    """Choose which downloaded model does each job. Images/scans are read again with the new choice."""
    _require_ready()
    s = load_settings()
    patch = body.model_dump(exclude_none=True)
    ready = lambda i: bool(model_store.get(i)) and model_store.is_installed(model_store.get(i))  # noqa: E731
    if patch.get("ocr_model") and (model_store.get(patch["ocr_model"]) or {}).get("kind") != "rapidocr":
        raise HTTPException(400, "Not a RapidOCR model")
    if patch.get("ocr_model") and not ready(patch["ocr_model"]):
        raise HTTPException(400, "Download that model first.")
    if "ocr_languages" in patch:
        langs = [c for c in dict.fromkeys(patch["ocr_languages"]) if c]
        if not langs:
            raise HTTPException(400, "Keep at least one language.")
        missing = [c for c in langs if not (model_store.tessdata_dir() / f"{c}.traineddata").is_file()]
        if missing:
            raise HTTPException(400, f"Download the language pack first: {', '.join(missing)}")
        patch["ocr_languages"] = langs
    eng = patch.get("ocr_engine")
    if eng == "tesseract" and not ocr_engines.is_available("tesseract"):
        raise HTTPException(400, "Download Tesseract first.")
    if eng == "rapidocr" and not ready(patch.get("ocr_model") or s["ocr_model"]):
        raise HTTPException(400, "Download a RapidOCR model first.")
    img = patch.get("image_model")
    if img and img != "off":
        item = model_store.get(img)
        if not item or item["role"] != "image":
            raise HTTPException(400, "Not an image-search model")
        if not model_store.is_installed(item):
            raise HTTPException(400, "Download that model first.")
    ocr_changed = any(k in patch and patch[k] != s[k] for k in ("ocr_engine", "ocr_model", "ocr_languages"))
    img_changed = img is not None and img != s["image_model"]
    s.update(patch)
    save_settings(s)
    _push_active(s)
    ocr_engines.set_engine(s["ocr_engine"])
    if img_changed:
        se.reset_encoder_error()
        if se.ensure_vector_dim():
            pipeline.reindex()                 # different vector size: everything is read again
        else:
            pipeline.rescan_ocr_files()        # same size: re-encode images/scans with the new model
        threading.Thread(target=se.warm_up, daemon=True, name="encoder-load").start()
    elif ocr_changed:
        pipeline.rescan_ocr_files()
    return {**model_store.catalog(), "rebuilding": img_changed}


@app.post("/api/ocr/install-tesseract")
def api_install_tesseract():
    """Install the Tesseract program (Windows: winget or the official installer)."""
    ocr_engines.tesseract_installer.start(on_done=lambda: ocr_engines.find_tesseract(refresh=True))
    return _models_info()


@app.get("/api/update")
def api_update():
    return updater.info()


@app.post("/api/update/check")
def api_update_check():
    """Ask the release server now (runs in FastAPI's thread pool; a few seconds at most)."""
    return updater.check()


@app.post("/api/update/download")
def api_update_download():
    try:
        updater.download()
    except UpdateError as e:
        raise HTTPException(409, str(e))
    return updater.info()


@app.post("/api/update/install")
def api_update_install():
    """Start the downloaded installer; the app closes itself a moment later."""
    try:
        updater.install()
    except UpdateError as e:
        raise HTTPException(409, str(e))
    return updater.info()


class TokenIn(BaseModel):
    token: str = ""


@app.post("/api/update/token")
def api_update_token(body: TokenIn):
    """Read-only GitHub token for a private repository (empty = remove). Stored on this PC only."""
    from backend.updater import set_token
    try:
        set_token(body.token)
    except UpdateError as e:
        raise HTTPException(400, str(e))
    return updater.info()


class RepoIn(BaseModel):
    repo: str = ""


@app.post("/api/update/repo")
def api_update_repo(body: RepoIn):
    """Choose which GitHub repository updates come from (owner/name or a github.com link; empty = built-in)."""
    try:
        return updater.set_repo(body.repo)
    except UpdateError as e:
        raise HTTPException(400, str(e))


@app.post("/api/update/ack")
def api_update_ack():
    updater.ack_updated()
    return {"ok": True}


def _mount_ui():
    """Serve the built React app from the same origin (no CORS, no port handshake)."""
    ui = Path(os.environ.get("DEEPFIND_UI_DIR", Path(__file__).resolve().parent.parent / "frontend" / "dist"))
    if (ui / "index.html").is_file():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=str(ui), html=True), name="ui")


_mount_ui()   # after every /api route, so the API always wins


def _watch_parent(pid: int):
    """Exit when the desktop shell that spawned us dies (killed from Task Manager, crash...),
    so no orphaned deepfind-api.exe keeps the index locked."""
    import psutil
    def loop():
        while True:
            if not psutil.pid_exists(pid):
                logger.warning("parent %s is gone — shutting down", pid)
                try:
                    pipeline.stop()
                finally:
                    os._exit(0)
            threading.Event().wait(2)
    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    ap.add_argument("--parent-pid", type=int, default=0, help="exit when this process exits")
    args = ap.parse_args()
    if args.parent_pid:
        _watch_parent(args.parent_pid)
    port = args.port or _free_port()
    print(f"DEEPFIND_PORT={port}", flush=True)     # for scripts that launch the API on a random port
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")