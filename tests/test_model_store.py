"""Settings -> Models: catalogue, custom models, choosing what is active, download queue."""
import time

import pytest

from backend import model_store as ms


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFIND_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DEEPFIND_CLIP_CACHE_DIR", str(tmp_path / "models" / "clip_cache"))
    monkeypatch.setenv("DEEPFIND_OCR_MODELS_DIR", str(tmp_path / "models" / "ocr_en"))
    ms.set_active(**ms.DEFAULT_ACTIVE)
    ms.jobs.errors.clear()
    return tmp_path / "models"


def test_catalog_lists_roles_and_nothing_installed(store):
    c = ms.catalog()
    assert {r["id"] for r in c["roles"]} == {"ocr", "image"}
    ids = {i["id"] for i in c["items"]}
    assert {"rapidocr-en", "tesseract", "tess-hin", "clip-vit-b-32", "jina-clip-v1"} <= ids
    assert all(i["status"] != "installed" for i in c["items"] if i["kind"] != "tesseract")
    assert all("files" not in i for i in c["items"])


def test_custom_models_validated_and_stored(store):
    with pytest.raises(ValueError):
        ms.add_custom({"kind": "tess-lang", "name": "x", "code": "h!"})
    with pytest.raises(ValueError):
        ms.add_custom({"kind": "rapidocr", "name": "x", "det_url": "http://a", "rec_url": "https://b"})
    with pytest.raises(ValueError):
        ms.add_custom({"kind": "clip", "name": "x", "repo": "a/b", "dim": 5})
    t = ms.add_custom({"kind": "tess-lang", "name": "Traditional Chinese", "code": "chi_tra"})
    assert t["url"].endswith("/chi_tra.traineddata") and t["requires"] == "tesseract"
    c = ms.add_custom({"kind": "clip", "name": "My CLIP", "repo": "me/clip", "dim": 768})
    assert c["text_file"] == "onnx/text_model.onnx" and c["image_repo"] == "me/clip"
    assert {t["id"], c["id"]} <= {i["id"] for i in ms.all_items() if i["custom"]}
    ms.delete_custom(t["id"])
    assert ms.get(t["id"]) is None


def test_installed_detection_and_active_paths(store):
    d = store / "ocr_en"
    d.mkdir(parents=True)
    assert ms.rapidocr_files() is None
    for n in ("en_PP-OCRv3_det_infer.onnx", "en_PP-OCRv3_rec_infer.onnx"):
        (d / n).write_bytes(b"x")
    assert ms.rapidocr_files()["det"].endswith("en_PP-OCRv3_det_infer.onnx")
    snap = store / "clip_cache" / "models--Qdrant--clip-ViT-B-32-text" / "snapshots" / "1"
    snap.mkdir(parents=True)
    (snap / "model.onnx").write_bytes(b"x")
    assert not ms.is_installed(ms.get("clip-vit-b-32"))           # image half still missing
    snap2 = store / "clip_cache" / "models--Qdrant--clip-ViT-B-32-vision" / "snapshots" / "1"
    snap2.mkdir(parents=True)
    (snap2 / "model.onnx").write_bytes(b"x")
    assert ms.is_installed(ms.get("clip-vit-b-32"))
    (store / "tessdata").mkdir()
    (store / "tessdata" / "hin.traineddata").write_bytes(b"x")
    ms.set_active(ocr_engine="tesseract", ocr_languages=["eng", "hin"])
    assert ms.tesseract_langs() == "hin"                           # only packs on disk
    ms.set_active(image_model="off")
    assert ms.image_spec() is None


def test_queue_downloads_only_ticked_items_engine_first(store, monkeypatch):
    done = []

    def fake_install(item, report, say):
        if item["kind"] == "tess-lang":
            (ms.tessdata_dir()).mkdir(parents=True, exist_ok=True)
            (ms.tessdata_dir() / f"{item['code']}.traineddata").write_bytes(b"x")
        done.append(item["id"])

    monkeypatch.setattr(ms, "install", fake_install)
    monkeypatch.setattr(ms, "is_installed", lambda it: it["id"] in done)
    ms.jobs.enqueue(["tess-tam"])
    for _ in range(100):
        if ms.jobs.info()["current"] is None and not ms.jobs.info()["queue"]:
            break
        time.sleep(0.02)
    assert done == ["tesseract", "tess-tam"]


def test_switching_to_a_model_with_another_vector_size_rebuilds_the_table(store, tmp_path):
    import search_engine as se
    ms.set_active(image_model="clip-vit-b-32")
    se.init_db(str(tmp_path / "db"), preload_encoder=False)
    assert se.VECTOR_DIM == 512 and not se.ensure_vector_dim()
    se.upsert({"file_path": "/x/a.png", "file_name": "a.png", "file_type": "png", "content": "cat", "vector": None})
    ms.set_active(image_model="jina-clip-v1")                  # 768 numbers per vector
    assert se.ensure_vector_dim() and se.VECTOR_DIM == 768
    assert not se.exists("/x/a.png")                            # emptied -> everything is read again
    se.upsert({"file_path": "/x/b.png", "file_name": "b.png", "file_type": "png", "content": "dog", "vector": [0.1] * 768})
    assert se.exists("/x/b.png")
    ms.set_active(image_model="clip-vit-b-32")
    assert se.ensure_vector_dim() and se.VECTOR_DIM == 512
