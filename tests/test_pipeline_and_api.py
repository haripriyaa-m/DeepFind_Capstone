import os
import sys
import time
import types

import pymupdf
import pytest
from PIL import Image

from conftest import fake_vec


@pytest.fixture()
def fake_vision(monkeypatch):
    mod = types.ModuleType("vision_service")
    def process_media_file(path):
        if path.endswith(".png"):
            return {"file_path": path, "ocr_text": "invoice total 4500 rupees",
                    "image_vector": fake_vec("scanned invoice on a desk").tolist(), "is_scanned": False}
        return {"file_path": path, "ocr_text": "scanned contract clause seven",
                "image_vector": None, "is_scanned": True}
    mod.process_media_file = process_media_file
    monkeypatch.setitem(sys.modules, "vision_service", mod)


def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def build_folder(d):
    d.mkdir()
    (d / "notes.txt").write_text("quarterly planning notes about zebra migration")
    Image.new("RGB", (60, 60), "white").save(d / "photo.png")
    pdf = pymupdf.open(); pg = pdf.new_page()
    Image.new("RGB", (80, 80), "white").save(d / "_s.png"); pg.insert_image(pg.rect, filename=str(d / "_s.png"))
    pdf.save(d / "scan.pdf"); (d / "_s.png").unlink()


def test_end_to_end_pipeline(db, tmp_path, fake_vision):
    import ingestion_service as isvc
    from pipeline import Pipeline
    isvc.reset_cache()
    folder = tmp_path / "docs"; build_folder(folder)
    p = Pipeline(); p.start([str(folder)], 100, 100, False)
    try:
        assert wait_for(lambda: db.count() == 3 and p.tier2_processed == 2)
        assert [r.file_name for r in db.search("zebra")] == ["notes.txt"]
        assert [r.file_name for r in db.search("invoice")] == ["photo.png"]          # OCR text indexed
        assert [r.file_name for r in db.search("clause")] == ["scan.pdf"]             # scanned PDF OCR'd
        sem = db.search("scanned invoice on a desk")                                   # CLIP-style image match
        assert sem[0].file_name == "photo.png"
    finally:
        p.stop()


def test_tier2_waits_while_system_busy(db, tmp_path, fake_vision, monkeypatch):
    import ingestion_service as isvc
    from pipeline import Pipeline
    isvc.reset_cache()
    folder = tmp_path / "docs"; build_folder(folder)
    monkeypatch.setattr(isvc, "should_pause_tier2", lambda: True)
    p = Pipeline(); p.start([str(folder)], 100, 100, False)
    try:
        assert wait_for(lambda: db.count() == 1)          # text indexed immediately
        time.sleep(2.5)
        assert db.count() == 1 and p.tier2_processed == 0  # heavy work held back
    finally:
        p.stop()


def test_image_still_searchable_by_name_when_vision_missing(db, tmp_path, monkeypatch):
    import ingestion_service as isvc
    from pipeline import Pipeline
    isvc.reset_cache()
    monkeypatch.setitem(sys.modules, "vision_service", None)   # import fails
    folder = tmp_path / "d"; folder.mkdir(); Image.new("RGB", (30, 30)).save(folder / "whiteboard_photo.png")
    p = Pipeline(); p.start([str(folder)], 100, 100, False)
    try:
        assert wait_for(lambda: db.count() == 1 and p.tier2_failed == 1)
        assert db.search("whiteboard_photo") or db.search("whiteboard")
        assert len(isvc._service.hash_cache) == 0                # not committed -> retried later
    finally:
        p.stop()


def test_api(db, tmp_path, fake_vision, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as bm
    import ingestion_service as isvc
    isvc.reset_cache()
    folder = tmp_path / "docs"; build_folder(folder)
    revealed = []
    monkeypatch.setattr(bm, "_reveal", revealed.append)
    with TestClient(bm.app) as c:
        assert wait_for(lambda: c.get("/api/health").json()["ready"])
        assert c.get("/api/search", params={"q": ""}).json()["results"] == []
        r = c.post("/api/settings", json={"watched_folders": [str(folder), "/does/not/exist"],
                                          "cpu_limit": 90, "ram_limit": 90, "pause_on_battery": False})
        assert r.status_code == 200 and r.json()["invalid_folders"] == ["/does/not/exist"]
        assert wait_for(lambda: c.get("/api/status").json().get("db_rows") == 3)
        hits = c.get("/api/search", params={"q": "zebra"}).json()["results"]
        assert hits and hits[0]["file_name"] == "notes.txt" and hits[0]["match_type"] == "keyword"
        st = c.get("/api/status").json()
        assert "Indexed" in st["message"] and st["ready"]
        assert c.post("/api/reveal", json={"file_path": hits[0]["file_path"]}).status_code == 200
        assert revealed == [hits[0]["file_path"]]
        assert c.post("/api/reveal", json={"file_path": "/etc/passwd"}).status_code == 404
        assert c.post("/api/settings", json={"cpu_limit": 1}).status_code == 422
        c.post("/api/settings", json={"watched_folders": []})              # removing folder purges its files
        assert wait_for(lambda: c.get("/api/status").json().get("db_rows") == 0)


def test_api_desktop_endpoints(db, tmp_path, fake_vision, monkeypatch):
    """Endpoints the desktop UI uses: open, thumb, library, health version."""
    from fastapi.testclient import TestClient
    import backend.main as bm
    import ingestion_service as isvc
    isvc.reset_cache()
    folder = tmp_path / "docs"; build_folder(folder)
    opened = []
    monkeypatch.setattr(bm, "_open", opened.append)
    with TestClient(bm.app) as c:
        assert wait_for(lambda: c.get("/api/health").json()["ready"])
        assert c.get("/api/health").json()["version"] == bm.API_VERSION
        c.post("/api/settings", json={"watched_folders": [str(folder)], "cpu_limit": 100,
                                      "ram_limit": 100, "pause_on_battery": False})
        assert wait_for(lambda: c.get("/api/library").json()["total"] == 3)
        lib = c.get("/api/library").json()
        assert lib["types"] == {"txt": 1, "png": 1, "pdf": 1}
        assert lib["folders"] == {str(folder): 3}

        png, pdf, txt = (str(folder / n) for n in ("photo.png", "scan.pdf", "notes.txt"))
        for f in (png, pdf):
            r = c.get("/api/thumb", params={"path": f, "size": 64})
            assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
            assert r.content[:2] == b"\xff\xd8"
        assert c.get("/api/thumb", params={"path": txt}).status_code == 415
        assert c.get("/api/thumb", params={"path": "/etc/hosts"}).status_code == 404   # not indexed

        assert c.post("/api/open", json={"file_path": txt}).status_code == 200
        assert opened == [txt]
        assert c.post("/api/open", json={"file_path": "/etc/passwd"}).status_code == 404
        c.post("/api/settings", json={"watched_folders": []})
        assert wait_for(lambda: c.get("/api/library").json()["total"] == 0)


def test_models_endpoint_and_ui_mount(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as bm
    from backend import models as bmodels
    monkeypatch.setenv("DEEPFIND_CLIP_CACHE_DIR", str(tmp_path / "m" / "clip_cache"))
    monkeypatch.setenv("DEEPFIND_OCR_MODELS_DIR", str(tmp_path / "m" / "ocr_en"))
    ui = tmp_path / "dist"; ui.mkdir(); (ui / "index.html").write_text("<!doctype html><title>DeepFind</title>")
    monkeypatch.setenv("DEEPFIND_UI_DIR", str(ui))
    bm._mount_ui()
    with TestClient(bm.app) as c:
        m = c.get("/api/models").json()
        assert m["installed"] is False and m["parts"] == {"text": False, "vision": False, "ocr": False}
        assert m["job"]["state"] == "idle"
        # fake a finished install on disk -> detected without restart
        (tmp_path / "m" / "ocr_en").mkdir(parents=True)
        for f in ("en_PP-OCRv3_det_infer.onnx", "en_PP-OCRv3_rec_infer.onnx"):
            (tmp_path / "m" / "ocr_en" / f).write_bytes(b"x")
        d = tmp_path / "m" / "clip_cache" / "models--Qdrant--clip-ViT-B-32-text" / "snap"; d.mkdir(parents=True)
        (d / "model.onnx").write_bytes(b"x")
        assert c.get("/api/models").json()["parts"] == {"text": True, "vision": False, "ocr": True}
        assert "DeepFind" in c.get("/").text                     # UI served from the same origin
        assert c.get("/api/health").json()["ok"]                  # API still wins over the static mount
    assert os.path.exists(bmodels._python_exe())
