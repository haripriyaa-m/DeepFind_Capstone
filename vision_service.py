"""
vision_service.py — Vision & OCR engine for DeepFind's Tier-2 (idle-time) pipeline.

Consumes images, scanned PDFs and videos (a few chosen frames each) and returns OCR text + a CLIP image vector
(512-dim, same space as the Qdrant/clip-ViT-B-32-text query encoder).

Constraints: 8GB RAM, CPU-only, fully offline, ONNX Runtime only (no PyTorch);
one item at a time; models loaded once. Scheduling (should_pause_tier2) lives in
the caller (pipeline.py) — this module is a pure "file in -> dict out" worker.

Behaviour notes:
  * OCR and CLIP failures are independent: if one model is missing you still get the other.
  * PDFs are handled PER PAGE: pages with a real text layer keep their digital text,
    only pages without one are rasterized + OCR'd (so mixed PDFs are fully covered).
  * The CLIP vector for a PDF represents page 1 only.
"""

from __future__ import annotations

import gc
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import pymupdf as fitz
from fastembed import ImageEmbedding
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

import video_frames
from backend import model_store, ocr_engines

logger = logging.getLogger("deepfind.vision_service")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
PDF_EXTENSIONS = {".pdf"}
VIDEO_EXTENSIONS = video_frames.VIDEO_EXTENSIONS

# Videos: only a few chosen frames are read (see video_frames.py). OCR is the slow part, so it
# only runs on the frames that look like text (slides, screen recordings), at most this many.
MAX_OCR_FRAMES = 48
OCR_TEXT_SCORE_MIN = 0.02
EMBED_BATCH = 16
VIDEO_TEXT_MAX_CHARS = 200_000

RASTER_DPI = 170
TEXT_LAYER_MIN_CHARS = 20
GC_EVERY_N_ITEMS = 15

OCR_MODELS_DIR = Path(os.environ.get("DEEPFIND_OCR_MODELS_DIR", "./models/ocr_en"))
DET_MODEL_PATH = OCR_MODELS_DIR / "en_PP-OCRv3_det_infer.onnx"
REC_MODEL_PATH = OCR_MODELS_DIR / "en_PP-OCRv3_rec_infer.onnx"
REC_KEYS_PATH = OCR_MODELS_DIR / "en_dict.txt"

CLIP_IMAGE_MODEL = "Qdrant/clip-ViT-B-32-vision"
CLIP_CACHE_DIR = Path(os.environ.get("DEEPFIND_CLIP_CACHE_DIR", "./models/clip_cache"))

_ocr_engine: Optional[RapidOCR] = None
_ocr_key: Optional[str] = None          # which RapidOCR set is loaded (Settings -> Models can switch it)
_clip_model: Optional[ImageEmbedding] = None
_clip_key: Optional[str] = None
_items_processed = 0


def _get_ocr_engine() -> RapidOCR:
    global _ocr_engine, _ocr_key
    files = model_store.rapidocr_files()      # the RapidOCR model set chosen in Settings -> Models
    if files is None:
        raise FileNotFoundError("RapidOCR model not downloaded. Download it in Settings -> Models.")
    key = f"{files['det']}|{files['rec']}|{files['keys']}"
    if _ocr_engine is None or key != _ocr_key:
        kwargs = dict(
            det_model_path=files["det"],
            rec_model_path=files["rec"],
            use_cls=False,               # skip angle classifier (upright scans/screenshots)
            intra_op_num_threads=2,
            inter_op_num_threads=1,
            det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False,
        )
        if files["keys"]:                # optional: some rec models embed their own dictionary
            kwargs["rec_keys_path"] = files["keys"]
        logger.info("Loading RapidOCR (%s, 2 threads)...", files["id"])
        _ocr_engine = RapidOCR(**kwargs)
        _ocr_key = key
    return _ocr_engine


def _get_clip_model() -> Optional[ImageEmbedding]:
    """Image half of the image-search model chosen in Settings -> Models (None = switched off)."""
    global _clip_model, _clip_key
    spec = model_store.image_spec()
    if spec is None:
        return None
    key = f"{spec['image_repo']}|{spec['image_file']}"
    if _clip_model is None or key != _clip_key:
        logger.info("Loading image encoder (%s)...", spec["image_repo"])
        CLIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        model_store.register_fastembed(spec)
        _clip_model = ImageEmbedding(model_name=spec["image_repo"],
                                     cache_dir=str(CLIP_CACHE_DIR), threads=2)
        _clip_key = key
    return _clip_model


def warm_up() -> None:
    if ocr_engines.get_engine() == "rapidocr":
        _get_ocr_engine()
    _get_clip_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_image_as_bgr(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        arr = np.array(im.convert("RGB"))
    return arr[:, :, ::-1].copy()


def _rasterize_page(page: "fitz.Page", dpi: int = RASTER_DPI) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    return img[:, :, ::-1].copy()


def _run_ocr(img_bgr: np.ndarray) -> str:
    """Read text with whichever engine is selected in Settings (rapidocr | tesseract | off)."""
    engine = ocr_engines.get_engine()
    if engine == "off":
        return ""
    if engine == "tesseract":
        return ocr_engines.run_tesseract(img_bgr)
    result, _ = _get_ocr_engine()(img_bgr)
    if not result:
        return ""
    return "\n".join(item[1] for item in result if item[1])


def _safe_ocr(img_bgr: np.ndarray) -> str:
    try:
        return _run_ocr(img_bgr)
    except Exception:
        logger.exception("OCR failed")
        return ""


def _compute_clip_vector_from_path(path: str) -> Optional[list]:
    model = _get_clip_model()
    if model is None:                    # image search switched off in Settings -> Models
        return None
    return next(iter(model.embed([path]))).tolist()


def _compute_clip_vector_from_array(img_bgr: np.ndarray) -> Optional[list]:
    """fastembed's ImageEmbedding wants a path, so spill to a temp JPEG."""
    if model_store.image_spec() is None:
        return None
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        Image.fromarray(img_bgr[:, :, ::-1]).save(tmp, format="JPEG", quality=90)
        return _compute_clip_vector_from_path(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _safe_vector(fn, arg) -> Optional[list]:
    try:
        return fn(arg)
    except Exception:
        logger.exception("CLIP embedding failed")
        return None


# ---------------------------------------------------------------------------
# Per-type processing
# ---------------------------------------------------------------------------

def _process_image(path: Path) -> dict:
    img = _load_image_as_bgr(path)
    ocr_text = _safe_ocr(img)
    del img
    return {"file_path": str(path), "ocr_text": ocr_text,
            "image_vector": _safe_vector(_compute_clip_vector_from_path, str(path)),
            "is_scanned": False}


def _process_pdf(path: Path) -> dict:
    doc = fitz.open(str(path))
    try:
        texts, first_img, ocr_used = [], None, False
        for i, page in enumerate(doc):
            if i >= 20:  # Prevent Tier 2 blocking for hours on massive scanned PDFs
                break
            digital = page.get_text("text").strip()
            need_ocr = len(digital) < TEXT_LAYER_MIN_CHARS
            img = _rasterize_page(page) if (i == 0 or need_ocr) else None
            if need_ocr:
                ocr_used = True
                combined = (digital + "\n" + _safe_ocr(img)).strip()
            else:
                combined = digital
            if combined:
                texts.append(combined)
            if i == 0:
                first_img = img
            else:
                del img
        vector = _safe_vector(_compute_clip_vector_from_array, first_img) if first_img is not None else None
        del first_img
        return {"file_path": str(path), "ocr_text": "\n\n".join(texts),
                "image_vector": vector, "is_scanned": ocr_used}
    finally:
        doc.close()


def _embed_jpegs(jpegs: list) -> list:
    """CLIP vectors for a list of JPEG byte strings (None where it failed / image search is off)."""
    out: list = [None] * len(jpegs)
    try:
        model = _get_clip_model()
        if model is None or not jpegs:
            return out
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, data in enumerate(jpegs):
                p = os.path.join(d, f"{i}.jpg")
                with open(p, "wb") as fh:
                    fh.write(data)
                paths.append(p)
            for i, vec in enumerate(model.embed(paths, batch_size=EMBED_BATCH)):
                out[i] = vec.tolist()
    except Exception:
        logger.exception("CLIP embedding of video frames failed")
    return out


def _process_video(path: Path) -> dict:
    """A video becomes a handful of frames: each with a CLIP vector (and OCR text when it looks
    like text). Returns the usual keys plus `segments` = [{"start", "vector", "text"}]."""
    sample = video_frames.extract_keyframes(str(path))
    frames = sample.frames
    vectors = _embed_jpegs([f.jpeg for f in frames])

    ocr_on: set = set()
    if frames and ocr_engines.get_engine() != "off":
        by_text = sorted(range(len(frames)), key=lambda i: -frames[i].text_score)[:MAX_OCR_FRAMES]
        ocr_on = {i for i in by_text if frames[i].text_score >= OCR_TEXT_SCORE_MIN}

    segments, texts, seen = [], [], set()
    for i, f in enumerate(frames):
        text = ""
        if i in ocr_on:
            with Image.open(io.BytesIO(f.jpeg)) as im:
                bgr = np.array(im.convert("RGB"))[:, :, ::-1].copy()
            text = _safe_ocr(bgr).strip()
            del bgr
        segments.append({"start": f.time, "vector": vectors[i], "text": text})
        key = " ".join(text.lower().split())
        if key and key not in seen:                 # a slide shown for a while is read once
            seen.add(key)
            texts.append(text)
    return {"file_path": str(path), "ocr_text": "\n".join(texts)[:VIDEO_TEXT_MAX_CHARS],
            "image_vector": None, "is_scanned": False, "segments": segments,
            "duration": sample.duration}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_media_file(file_path: str) -> dict:
    """Returns {"file_path", "ocr_text", "image_vector" (list|None), "is_scanned"}."""
    global _items_processed
    path = Path(file_path)
    ext = path.suffix.lower()
    empty = {"file_path": str(path), "ocr_text": "", "image_vector": None, "is_scanned": False}
    try:
        if ext in PDF_EXTENSIONS:
            result = _process_pdf(path)
        elif ext in IMAGE_EXTENSIONS:
            result = _process_image(path)
        elif ext in VIDEO_EXTENSIONS:
            result = _process_video(path)
        else:
            logger.warning("Unsupported file type routed to vision_service: %s", file_path)
            result = empty
    except Exception:
        logger.exception("vision_service failed on %s", file_path)
        result = empty

    _items_processed += 1
    if _items_processed % GC_EVERY_N_ITEMS == 0:
        gc.collect()
    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("Usage: python vision_service.py <file> [file ...]")
        sys.exit(1)
    warm_up()
    for f in sys.argv[1:]:
        out = process_media_file(f)
        print(f"{out['file_path']}: is_scanned={out['is_scanned']}, ocr_chars={len(out['ocr_text'])}, "
              f"vector_dim={len(out['image_vector']) if out['image_vector'] else None}")