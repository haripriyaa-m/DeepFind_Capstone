"""Tests for: indexing schedule, OCR engine choice, and the ranking/score fields on results."""

import os
import stat
import sys
import time

import numpy as np
import pytest
from PIL import Image

from conftest import fake_vec


def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(autouse=True)
def clean_state():
    """The ingestion service, OCR choice and settings.json are process-wide: reset around each test."""
    import backend.main as bm
    import ingestion_service as isvc
    from backend import ocr_engines
    def reset():
        isvc._service._defer = False          # (not set_deferred: that would start a scan)
        isvc._service._deferred.clear()
        ocr_engines.set_engine("rapidocr")
        try:
            bm._settings_path().unlink()
        except OSError:
            pass
    reset()
    yield
    reset()


# ----------------------------------------------------------------------------------------
# Indexing schedule — ingestion level
# ----------------------------------------------------------------------------------------

def test_deferred_mode_remembers_changes_until_index_now(tmp_path):
    import ingestion_service as isvc
    got = []
    svc = isvc.IngestionService(data_dir=tmp_path / "d")
    svc.register_appu_callback(got.append)
    folder = tmp_path / "docs"; folder.mkdir()
    (folder / "old.txt").write_text("already here")
    svc.set_deferred(True)
    svc.start_watcher([str(folder)], scan=False)
    try:
        time.sleep(0.5)
        assert got == [] and svc.pending_changes() == 0        # nothing read at start

        (folder / "new.txt").write_text("fresh note about giraffes")
        assert wait_for(lambda: svc.pending_changes() == 1)    # remembered, not read
        time.sleep(1.0)
        assert got == []

        svc.index_now()
        assert wait_for(lambda: {d["file_name"] for d in got} == {"old.txt", "new.txt"})
        assert svc.pending_changes() == 0
    finally:
        svc.stop_watcher()


def test_deletes_are_applied_even_when_deferred(tmp_path):
    import ingestion_service as isvc
    got = []
    svc = isvc.IngestionService(data_dir=tmp_path / "d")
    svc.register_appu_callback(got.append)
    folder = tmp_path / "docs"; folder.mkdir()
    victim = folder / "gone.txt"; victim.write_text("temporary")
    svc.set_deferred(True)
    svc.start_watcher([str(folder)], scan=False)
    try:
        victim.unlink()
        assert wait_for(lambda: any(d.get("deleted") for d in got))      # not held back
        (folder / "a.txt").write_text("x")
        assert wait_for(lambda: svc.pending_changes() == 1)
        (folder / "a.txt").unlink()
        assert wait_for(lambda: svc.pending_changes() == 0)              # forgotten with the file
    finally:
        svc.stop_watcher()


def test_leaving_deferred_mode_reads_what_piled_up(tmp_path):
    import ingestion_service as isvc
    got = []
    svc = isvc.IngestionService(data_dir=tmp_path / "d")
    svc.register_appu_callback(got.append)
    folder = tmp_path / "docs"; folder.mkdir()
    svc.set_deferred(True)
    svc.start_watcher([str(folder)], scan=False)
    try:
        (folder / "late.txt").write_text("written while waiting")
        assert wait_for(lambda: svc.pending_changes() == 1)
        svc.set_deferred(False)                                          # back to "as files change"
        assert wait_for(lambda: any(d.get("file_name") == "late.txt" for d in got))
    finally:
        svc.stop_watcher()


# ----------------------------------------------------------------------------------------
# Indexing schedule — pipeline level
# ----------------------------------------------------------------------------------------

def test_manual_schedule_indexes_only_on_request(db, tmp_path):
    import ingestion_service as isvc
    from pipeline import Pipeline
    isvc.reset_cache()
    folder = tmp_path / "docs"; folder.mkdir()
    (folder / "notes.txt").write_text("quarterly planning about zebra migration")
    p = Pipeline(); p.start([str(folder)], 100, 100, False, index_schedule="manual")
    try:
        time.sleep(1.5)
        assert db.count() == 0 and p.next_index_run is None     # nothing happens by itself
        p.index_now()
        assert wait_for(lambda: db.count() == 1)
        assert p.last_index_run > 0
        (folder / "more.txt").write_text("another file about giraffes")
        time.sleep(1.5)
        assert db.count() == 1                                  # still waiting for the next click
        p.index_now()
        assert wait_for(lambda: db.count() == 2)
    finally:
        p.stop()


def test_new_folder_is_read_straight_away_even_in_manual_mode(db, tmp_path):
    import ingestion_service as isvc
    from pipeline import Pipeline
    isvc.reset_cache()
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir(); (b / "x.txt").write_text("hello from folder b")
    p = Pipeline(); p.start([str(a)], 100, 100, False, index_schedule="manual")
    try:
        p.apply_settings([str(a), str(b)], 100, 100, False)
        assert wait_for(lambda: db.count() == 1)
    finally:
        p.stop()


def test_interval_schedule_runs_when_overdue_and_waits_when_recent(db, tmp_path, monkeypatch):
    import ingestion_service as isvc
    import pipeline as pl
    isvc.reset_cache()
    monkeypatch.setattr(pl, "SCHEDULE_TICK_SEC", 0.2)
    folder = tmp_path / "docs"; folder.mkdir()
    (folder / "n.txt").write_text("hourly indexing test")

    recent = pl.Pipeline()
    now = time.time()
    recent.start([str(folder)], 100, 100, False, index_schedule="1h", last_index_run=now - 60)
    try:
        time.sleep(1.2)
        assert db.count() == 0                                   # next run is ~59 min away
        assert abs(recent.next_index_run - (now - 60 + 3600)) < 2
    finally:
        recent.stop()

    overdue = pl.Pipeline()
    saved = []
    overdue.on_index_run = saved.append
    overdue.start([str(folder)], 100, 100, False, index_schedule="1h", last_index_run=now - 7200)
    try:
        assert wait_for(lambda: db.count() == 1)                 # overdue -> runs on the next tick
        assert saved and overdue.next_index_run > time.time() + 3000
    finally:
        overdue.stop()


# ----------------------------------------------------------------------------------------
# OCR engines
# ----------------------------------------------------------------------------------------

def test_engine_choice_is_validated():
    from backend import ocr_engines
    assert ocr_engines.get_engine() == "rapidocr"
    ocr_engines.set_engine("off")
    assert ocr_engines.get_engine() == "off"
    with pytest.raises(ValueError):
        ocr_engines.set_engine("magic")


def test_engines_info_reports_availability(monkeypatch):
    from backend import ocr_engines
    monkeypatch.setattr(ocr_engines, "find_tesseract", lambda refresh=False: (None, None))
    info = {e["id"]: e for e in ocr_engines.engines_info(rapidocr_models_ok=False)}
    assert set(info) == {"rapidocr", "tesseract", "off"}
    assert info["rapidocr"]["available"] is False and info["off"]["available"] is True
    assert info["tesseract"]["available"] is False and "Install" in info["tesseract"]["detail"]

    monkeypatch.setattr(ocr_engines, "find_tesseract", lambda refresh=False: ("/usr/bin/tesseract", "tesseract 5.3.0"))
    info = {e["id"]: e for e in ocr_engines.engines_info(rapidocr_models_ok=True)}
    assert info["tesseract"]["available"] is True and info["tesseract"]["detail"] == "tesseract 5.3.0"
    assert info["rapidocr"]["available"] is True


@pytest.mark.skipif(sys.platform.startswith("win"), reason="uses a shell-script stand-in for tesseract")
def test_run_tesseract_calls_the_program_and_returns_text(tmp_path, monkeypatch):
    from backend import ocr_engines
    fake = tmp_path / "tesseract"
    fake.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "tesseract 5.3.0"; exit 0; fi\n'
                    'echo "  Invoice total 4500  "; echo ""; echo "Pay by Friday"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("DEEPFIND_TESSERACT", str(fake))
    exe, ver = ocr_engines.find_tesseract(refresh=True)
    assert exe == str(fake) and ver == "tesseract 5.3.0"
    img = np.full((40, 80, 3), 255, dtype=np.uint8)
    assert ocr_engines.run_tesseract(img) == "Invoice total 4500\nPay by Friday"


def test_run_tesseract_without_the_program_returns_empty(monkeypatch):
    from backend import ocr_engines
    monkeypatch.setattr(ocr_engines, "find_tesseract", lambda refresh=False: (None, None))
    assert ocr_engines.run_tesseract(np.zeros((10, 10, 3), dtype=np.uint8)) == ""


def test_vision_service_uses_the_selected_engine(monkeypatch):
    vs = pytest.importorskip("vision_service")        # needs rapidocr + fastembed installed
    from backend import ocr_engines
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    monkeypatch.setattr(ocr_engines, "run_tesseract", lambda a: "from tesseract")
    monkeypatch.setattr(vs, "_get_ocr_engine", lambda: (lambda a: ([[None, "from rapid", 1.0]], None)))
    ocr_engines.set_engine("tesseract")
    assert vs._run_ocr(img) == "from tesseract"
    ocr_engines.set_engine("rapidocr")
    assert vs._run_ocr(img) == "from rapid"
    ocr_engines.set_engine("off")
    assert vs._run_ocr(img) == ""


def test_changing_engine_rereads_images_and_pdfs(db, tmp_path, monkeypatch):
    import ingestion_service as isvc
    from pipeline import Pipeline
    from backend import ocr_engines
    isvc.reset_cache()
    folder = tmp_path / "docs"; folder.mkdir()
    Image.new("RGB", (40, 40), "white").save(folder / "shot.png")
    (folder / "notes.txt").write_text("plain text")
    calls = []
    import types
    mod = types.ModuleType("vision_service")
    def process_media_file(path):
        calls.append(ocr_engines.get_engine())
        return {"file_path": path, "ocr_text": f"engine{ocr_engines.get_engine()}marker", "image_vector": None, "is_scanned": False}
    mod.process_media_file = process_media_file
    monkeypatch.setitem(sys.modules, "vision_service", mod)
    p = Pipeline(); p.start([str(folder)], 100, 100, False)
    try:
        assert wait_for(lambda: calls == ["rapidocr"] and p.tier2_processed == 1)
        p.apply_settings([str(folder)], 100, 100, False, ocr_engine="off")
        assert wait_for(lambda: calls == ["rapidocr", "off"])         # image was read again
        assert wait_for(lambda: [r.file_name for r in db.search("engineoffmarker")] == ["shot.png"])
        assert p.status()["ocr_engine"] == "off"
    finally:
        p.stop()


def test_tesseract_install_job_reports_progress_and_success(monkeypatch):
    from backend import ocr_engines
    steps = []
    def fake_install(progress=lambda m: None):
        progress("Downloading Tesseract… 5 of 25 MB"); steps.append("ran"); time.sleep(0.3)
        return "tesseract 5.5.3"
    monkeypatch.setattr(ocr_engines, "install_tesseract", fake_install)
    done = []
    job = ocr_engines.TesseractInstaller()
    assert job.info() == {"state": "idle", "message": ""}
    job.start(on_done=lambda: done.append(1))
    assert job.info()["state"] == "running"
    job.start()                                                  # a second click does nothing
    assert wait_for(lambda: job.info()["state"] == "done")
    assert steps == ["ran"] and done == [1] and "5.5.3" in job.info()["message"]


def test_tesseract_install_job_reports_failure_in_plain_words(monkeypatch):
    from backend import ocr_engines
    def boom(progress=lambda m: None):
        raise RuntimeError("Tesseract could not be installed automatically. Install it by hand.")
    monkeypatch.setattr(ocr_engines, "install_tesseract", boom)
    job = ocr_engines.TesseractInstaller(); job.start()
    assert wait_for(lambda: job.info()["state"] == "failed")
    assert "by hand" in job.info()["message"]


def test_install_tesseract_when_already_installed_is_a_no_op(monkeypatch):
    from backend import ocr_engines
    monkeypatch.setattr(ocr_engines, "find_tesseract", lambda refresh=False: ("/x/tesseract", "tesseract 5.3.4"))
    assert ocr_engines.install_tesseract() == "tesseract 5.3.4"


def test_install_tesseract_off_windows_explains_what_to_do(monkeypatch):
    from backend import ocr_engines
    monkeypatch.setattr(ocr_engines, "find_tesseract", lambda refresh=False: (None, None))
    monkeypatch.setattr(ocr_engines, "can_auto_install", lambda: False)
    with pytest.raises(RuntimeError) as e:
        ocr_engines.install_tesseract()
    assert "Windows" in str(e.value) and "Check again" in str(e.value)


def test_engines_info_marks_tesseract_installable_only_when_missing(monkeypatch):
    from backend import ocr_engines
    monkeypatch.setattr(ocr_engines, "can_auto_install", lambda: True)
    monkeypatch.setattr(ocr_engines, "find_tesseract", lambda refresh=False: (None, None))
    t = {e["id"]: e for e in ocr_engines.engines_info(True)}["tesseract"]
    assert t["installable"] is True and t["install_hint"]
    monkeypatch.setattr(ocr_engines, "find_tesseract", lambda refresh=False: ("/x", "tesseract 5"))
    t = {e["id"]: e for e in ocr_engines.engines_info(True)}["tesseract"]
    assert t["installable"] is False and t["install_hint"] == ""


def test_install_endpoint_starts_the_job_and_models_info_reports_it(monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as bm
    from backend import ocr_engines
    monkeypatch.setattr(ocr_engines, "install_tesseract", lambda progress=lambda m: None: (time.sleep(0.3), "tesseract 5.5.3")[1])
    monkeypatch.setattr(ocr_engines, "tesseract_installer", ocr_engines.TesseractInstaller())
    with TestClient(bm.app) as c:
        assert wait_for(lambda: c.get("/api/health").json()["ready"])
        r = c.post("/api/ocr/install-tesseract").json()
        assert r["tesseract_job"]["state"] == "running"
        assert wait_for(lambda: c.get("/api/models").json()["tesseract_job"]["state"] == "done")


# ----------------------------------------------------------------------------------------
# Settings API
# ----------------------------------------------------------------------------------------

def test_settings_api_schedule_engine_and_index_now(db, tmp_path):
    from fastapi.testclient import TestClient
    import backend.main as bm
    import ingestion_service as isvc
    from backend import ocr_engines
    isvc.reset_cache()
    folder = tmp_path / "docs"; folder.mkdir()
    (folder / "n.txt").write_text("schedule api test about okapi")
    with TestClient(bm.app) as c:
        assert wait_for(lambda: c.get("/api/health").json()["ready"])
        s = c.get("/api/settings").json()
        assert s["index_schedule"] == "realtime" and s["ocr_engine"] == "rapidocr"

        r = c.post("/api/settings", json={"watched_folders": [str(folder)], "cpu_limit": 100,
                                          "ram_limit": 100, "pause_on_battery": False})
        assert r.status_code == 200
        assert wait_for(lambda: bool(c.get("/api/search", params={"q": "okapi"}).json()["results"]))

        r = c.post("/api/settings", json={"index_schedule": "manual"})
        assert r.status_code == 200 and r.json()["index_schedule"] == "manual"
        st = c.get("/api/status").json()
        assert st["index_schedule"] == "manual" and st["next_index_run"] is None

        (folder / "late.txt").write_text("written after switching to manual, about quokkas")
        assert wait_for(lambda: c.get("/api/status").json()["pending_changes"] == 1)
        time.sleep(1.0)
        assert c.get("/api/search", params={"q": "quokkas"}).json()["results"] == []   # waiting for the next run

        assert c.post("/api/index-now").json() == {"ok": True}
        assert wait_for(lambda: bool(c.get("/api/search", params={"q": "quokkas"}).json()["results"]))
        assert c.get("/api/status").json()["pending_changes"] == 0
        assert c.get("/api/settings").json()["last_index_run"] > 0                    # remembered

        # bad values are rejected
        assert c.post("/api/settings", json={"index_schedule": "every-blue-moon"}).status_code == 422
        assert c.post("/api/settings", json={"ocr_engine": "magic"}).status_code == 422

        # an engine that can't run is refused with a helpful message; "off" always works
        import backend.ocr_engines as oe
        orig = oe.find_tesseract
        oe.find_tesseract = lambda refresh=False: (None, None)
        try:
            r = c.post("/api/settings", json={"ocr_engine": "tesseract"})
            assert r.status_code == 400 and "Tesseract" in r.json()["detail"]
        finally:
            oe.find_tesseract = orig
        assert c.post("/api/settings", json={"ocr_engine": "off"}).json()["ocr_engine"] == "off"
        assert ocr_engines.get_engine() == "off"

        m = c.get("/api/models").json()
        assert m["ocr_engine"] == "off" and {e["id"] for e in m["ocr_engines"]} == {"rapidocr", "tesseract", "off"}
        c.post("/api/settings", json={"watched_folders": []})


# ----------------------------------------------------------------------------------------
# Ranking / score fields
# ----------------------------------------------------------------------------------------

def _doc(path, content, vector=None):
    return {"file_path": path, "file_name": os.path.basename(path), "file_type": path.rsplit(".", 1)[-1],
            "content": content, "vector": vector, "modified_time": time.time()}


def test_results_carry_rank_and_match_percent(db):
    db.upsert(_doc("/d/a.txt", "invoice invoice invoice total for march"))
    db.upsert(_doc("/d/b.txt", "the invoice was paid"))
    db.upsert(_doc("/d/c.txt", "holiday photos and travel plans"))
    res = db.search("invoice")
    assert [r.file_name for r in res][:2] == ["a.txt", "b.txt"]
    assert [r.rank for r in res] == list(range(1, len(res) + 1))
    assert res[0].match_percent == 100
    pcts = [r.match_percent for r in res]
    assert pcts == sorted(pcts, reverse=True) and all(1 <= p <= 100 for p in pcts)
    assert res[0].keyword_rank == 1 and res[0].keyword_score and res[0].keyword_score > 0
    assert res[0].to_dict()["rank"] == 1


def test_hybrid_result_reports_both_kinds_of_evidence(db):
    # an image-like record whose vector equals the query's vector, plus a text record
    db.upsert(_doc("/d/IMG_0042.png", "", fake_vec("whiteboard architecture diagram").tolist()))
    db.upsert(_doc("/d/notes.txt", "whiteboard architecture diagram notes"))
    res = {r.file_name: r for r in db.search("whiteboard architecture diagram")}
    img, txt = res["IMG_0042.png"], res["notes.txt"]
    assert img.match_type == "semantic" and img.semantic_rank == 1 and img.keyword_rank is None
    assert img.similarity is not None and img.similarity > 0.99
    assert txt.match_type == "keyword" and txt.similarity is None
    assert img.rank in (1, 2) and txt.rank in (1, 2)