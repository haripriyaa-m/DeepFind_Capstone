"""fetch_model.py — download one image-search (CLIP-style) model pair into DEEPFIND_CLIP_CACHE_DIR.

Run by backend/model_store.py in a child process (the app itself stays offline):
    python scripts/fetch_model.py '{"text_repo": ..., "text_file": ..., "image_repo": ..., "image_file": ..., "dim": 512, "custom": false}'
After the download it encodes one sentence and one small picture and checks that both give vectors
of the expected size — so a wrong custom model is reported now, not during indexing.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    spec = json.loads(sys.argv[1])
    cache = os.environ.get("DEEPFIND_CLIP_CACHE_DIR", "./models/clip_cache")
    Path(cache).mkdir(parents=True, exist_ok=True)
    from backend.model_store import register_fastembed
    register_fastembed(spec)
    from fastembed import ImageEmbedding, TextEmbedding
    from PIL import Image

    print("Downloading text encoder", spec["text_repo"], flush=True)
    t = TextEmbedding(model_name=spec["text_repo"], cache_dir=cache)
    tv = next(iter(t.embed(["a photo of a dog"])))
    print("Downloading image encoder", spec["image_repo"], flush=True)
    im = ImageEmbedding(model_name=spec["image_repo"], cache_dir=cache)
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        Image.new("RGB", (64, 64), (200, 120, 40)).save(tmp)
        iv = next(iter(im.embed([tmp])))
    finally:
        os.remove(tmp)
    if len(tv) != spec["dim"] or len(iv) != spec["dim"]:
        print(f"Model check failed: text gives {len(tv)} numbers, image gives {len(iv)}, "
              f"expected {spec['dim']}. Check the vector size and file names.", flush=True)
        return 2
    print("OK", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                       # last line is shown to the user
        print(f"{type(e).__name__}: {e}", flush=True)
        sys.exit(1)
