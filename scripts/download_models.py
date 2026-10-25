"""
download_models.py — one-shot setup script to fetch every model DeepFind's
Vision & OCR Engine (and the paired text-embedding side) needs.

None of these are committed to git — they're large binaries, and the
target machine runs fully offline. Run this once on a machine WITH
internet access, then copy the whole repo (models/ folder included) to
the offline machine.

Usage:
    python scripts/download_models.py                  # RapidOCR + CLIP models
    python scripts/download_models.py --tesseract      # ...and also install Tesseract (Windows)
    python scripts/download_models.py --only-tesseract # just Tesseract

Tesseract is a program rather than a model file, so on Windows it is installed with winget
or the official installer (Windows asks for permission once). It is optional: DeepFind works
without it, you only need it if you pick "Tesseract" under Settings -> AI models.
"""

import argparse
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # so `backend` can be imported

OCR_DIR = Path(os.environ.get("DEEPFIND_OCR_MODELS_DIR", "./models/ocr_en"))
CLIP_CACHE_DIR = Path(os.environ.get("DEEPFIND_CLIP_CACHE_DIR", "./models/clip_cache"))

# English-only RapidOCR trio — NOT the default multilingual bundle rapidocr
# pulls in on its own.
OCR_FILES = {
    "en_PP-OCRv3_det_infer.onnx": (
        "https://huggingface.co/SWHL/RapidOCR/resolve/main/PP-OCRv4/en_PP-OCRv3_det_infer.onnx"
    ),
    "en_PP-OCRv3_rec_infer.onnx": (
        "https://huggingface.co/SWHL/RapidOCR/resolve/main/PP-OCRv3/en_PP-OCRv3_rec_infer.onnx"
    ),
    "en_dict.txt": (
        "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/release/2.7/ppocr/utils/en_dict.txt"
    ),
}


def download_ocr_models() -> None:
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in OCR_FILES.items():
        dest = OCR_DIR / filename
        if dest.exists():
            print(f"[skip] {dest} already exists")
            continue
        print(f"Downloading {filename} ...", flush=True)
        part = dest.with_suffix(dest.suffix + ".part")      # never leave a half file that looks installed
        urllib.request.urlretrieve(url, part)
        os.replace(part, dest)
        print(f"  -> {dest}", flush=True)


def download_clip_models() -> None:
    # Imported here so this script's OCR-only half still works even if
    # fastembed isn't installed yet on whoever's running it.
    from fastembed import ImageEmbedding, TextEmbedding

    print("Downloading Qdrant/clip-ViT-B-32-vision (image side, used by vision_service.py) ...", flush=True)
    ImageEmbedding(model_name="Qdrant/clip-ViT-B-32-vision", cache_dir=str(CLIP_CACHE_DIR))

    print("Downloading Qdrant/clip-ViT-B-32-text (text side, used by the search/query path) ...", flush=True)
    TextEmbedding(model_name="Qdrant/clip-ViT-B-32-text", cache_dir=str(CLIP_CACHE_DIR))


def install_tesseract() -> bool:
    from backend import ocr_engines
    exe, ver = ocr_engines.find_tesseract(refresh=True)
    if exe:
        print(f"[skip] Tesseract already installed: {ver} ({exe})", flush=True)
        return True
    if not ocr_engines.can_auto_install():
        print("Tesseract is not installed. " + ocr_engines.manual_install_hint(), flush=True)
        return False
    print("Installing Tesseract (Windows may ask for permission - click Yes) ...", flush=True)
    try:
        ver = ocr_engines.install_tesseract(lambda m: print("  " + m, flush=True))
    except Exception as e:
        print(f"Tesseract install failed: {e}", flush=True)
        return False
    print(f"  -> {ver}", flush=True)
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Download DeepFind's models.")
    ap.add_argument("--tesseract", action="store_true", help="also install Tesseract OCR (Windows)")
    ap.add_argument("--only-tesseract", action="store_true", help="install Tesseract and skip the models")
    args = ap.parse_args()

    ok = True
    if not args.only_tesseract:
        download_ocr_models()
        download_clip_models()
    if args.tesseract or args.only_tesseract:
        ok = install_tesseract()
    print("\nDone. Copy the models/ folder alongside the repo onto the offline machine.")
    sys.exit(0 if ok else 1)