"""Video indexing + search, and the strength-based ranking. No models: vectors are faked."""
import time

import numpy as np
import pytest

from conftest import fake_vec

av = pytest.importorskip("av")
import video_frames as vf
from _videomaker import make_video

RED, GREEN, BLUE = (220, 20, 20), (20, 200, 20), (20, 20, 220)


# ---- picking frames ------------------------------------------------------------------------

def test_one_frame_per_scene_not_per_frame(tmp_path):
    p = tmp_path / "scenes.mp4"
    make_video(p, [RED] * 4 + [GREEN] * 4 + [BLUE] * 4, fps=5)      # 60 frames, 3 scenes
    s = vf.extract_keyframes(str(p))
    assert 3 <= len(s.frames) <= 5 and s.frames[0].time == 0.0
    assert abs(s.duration - 12) < 1 and all(f.jpeg[:2] == b"\xff\xd8" for f in s.frames)
    assert all(b.time - a.time >= vf.MIN_GAP_SEC for a, b in zip(s.frames, s.frames[1:]))


def test_long_static_video_is_sampled_by_seeking(tmp_path):
    p = tmp_path / "talk.mp4"
    make_video(p, [RED] * 70, fps=2, gop=1000)                     # one key frame in 70 s
    times = [f.time for f in vf.extract_keyframes(str(p)).frames]
    assert times[0] == 0.0 and 28 <= times[1] <= 32 and 58 <= times[2] <= 62 and len(times) == 3


def test_frame_cap(tmp_path):
    p = tmp_path / "many.mp4"
    cols = [RED, GREEN, BLUE, (200, 200, 20), (20, 200, 200), (200, 20, 200)] * 3
    make_video(p, cols, fps=5, gop=5)
    assert len(vf.extract_keyframes(str(p), max_frames=4).frames) == 4


# ---- searching videos ----------------------------------------------------------------------

def _video(path, segs, content=""):
    return {"file_path": path, "file_name": path.rsplit("/", 1)[-1], "file_type": "mp4",
            "content": content, "vector": None, "modified_time": time.time(), "segments": segs}


def test_video_found_by_picture_with_timestamp(db):
    db.upsert(_video("/v/trip.mp4", [
        {"start": 3.0, "vector": fake_vec("mountain road").tolist(), "text": ""},
        {"start": 12.0, "vector": fake_vec("sunset beach").tolist(), "text": ""}]))
    res = db.search("sunset beach")
    assert res[0].file_name == "trip.mp4" and res[0].match_type == "semantic"
    assert res[0].start_sec == 12.0 and res[0].similarity > 0.99
    assert db.search("mountain road")[0].start_sec == 3.0


def test_video_found_by_words_on_screen(db):
    db.upsert(_video("/v/review.mp4", [
        {"start": 5.0, "vector": fake_vec("title slide").tolist(), "text": "Welcome everyone"},
        {"start": 40.0, "vector": fake_vec("chart").tolist(), "text": "Quarterly revenue chart"}],
        content="Welcome everyone\nQuarterly revenue chart"))
    res = db.search("revenue")
    assert res[0].file_name == "review.mp4" and res[0].match_type == "keyword" and res[0].start_sec == 40.0


def test_replacing_and_deleting_a_video_removes_old_frames(db):
    path = "/v/a.mp4"
    db.upsert(_video(path, [{"start": 1.0, "vector": fake_vec("old scene").tolist(), "text": ""}]))
    db.upsert(_video(path, [{"start": 2.0, "vector": fake_vec("new scene").tolist(), "text": ""}]))
    assert db._segments.count_rows() == 1
    db.delete(path)
    assert db._segments.count_rows() == 0 and db.search("new scene") == []
    db.upsert(_video("/v/dir/b.mp4", [{"start": 1.0, "vector": fake_vec("x y").tolist(), "text": ""}]))
    db.delete("/v/dir", recursive=True)
    assert db._segments.count_rows() == 0


# ---- ranking: strength, not just list position -----------------------------------------------

def _vec_with_cosine(text, c):
    q = fake_vec(text)
    o = fake_vec("something unrelated")
    o = o - (o @ q) * q
    o /= np.linalg.norm(o)
    return (c * q + np.sqrt(1 - c * c) * o).tolist()


def _doc(path, content, vector=None, ftype="txt"):
    return {"file_path": path, "file_name": path.rsplit("/", 1)[-1], "file_type": ftype,
            "content": content, "vector": vector, "modified_time": time.time()}


def test_keyword_heavy_file_is_not_beaten_by_a_weak_picture_match(db):
    filler = " ".join(f"word{i}" for i in range(40))
    db.upsert(_doc("/d/topic.txt", "topic"))                                   # #1: name + word
    db.upsert(_doc("/d/report.txt", "topic " * 6 + filler))                    # many mentions
    db.upsert(_doc("/d/IMG_1.png", "", _vec_with_cosine("topic", 0.27), "png"))  # weak CLIP hit
    for i in range(5):
        db.upsert(_doc(f"/d/n{i}.txt", filler))
    names = [r.file_name for r in db.search("topic")]
    # many mentions beat one mention, and both beat a weak picture guess (old order: image 2nd)
    assert names[:3] == ["report.txt", "topic.txt", "IMG_1.png"], names


def test_a_very_strong_picture_match_can_still_win(db):
    db.upsert(_doc("/d/notes.txt", "a note that mentions the sunset once among many other words " * 3))
    db.upsert(_doc("/d/IMG_9.png", "", fake_vec("sunset over the sea").tolist(), "png"))
    assert db.search("sunset over the sea")[0].file_name == "IMG_9.png"


def test_weak_matches_are_dropped_for_one_word_queries(db):
    db.upsert(_doc("/d/IMG_2.png", "", _vec_with_cosine("topic", 0.22), "png"))
    assert db.search("topic") == []


# ---- ingestion routing ---------------------------------------------------------------------

def test_video_is_findable_by_name_at_once_and_queued_for_frames(tmp_path):
    import ingestion_service as isvc
    folder = tmp_path / "vids"; folder.mkdir()
    (folder / "holiday.mp4").write_bytes(b"not really a video" * 100)
    got = []
    svc = isvc.IngestionService(data_dir=tmp_path / "d")
    svc.register_appu_callback(got.append)
    svc.start_watcher([str(folder)])
    try:
        assert svc.wait_idle(15)
        assert [d["file_name"] for d in got] == ["holiday.mp4"] and got[0]["file_type"] == "mp4"
        item = svc.get_tier2_queue().get(timeout=5)
        assert item["file_path"].endswith("holiday.mp4") and item["needs_ocr"] is True
        assert not svc.hash_cache.is_unchanged(item["file_path"])      # not done until frames are read
        assert isvc.HashCache.signature(item["file_path"], ".mp4").count(":") == 1   # size:mtime, no GBs read
    finally:
        svc.stop_watcher()


def test_scheduled_scan_skips_unchanged_files_without_rereading(tmp_path):
    import ingestion_service as isvc
    f = tmp_path / "a.txt"; f.write_text("hello")
    cache = isvc.HashCache(tmp_path / "h.json")
    assert not cache.is_unchanged(str(f), ".txt")
    cache.mark_indexed(str(f))
    calls = []
    orig = isvc.HashCache.hash_file
    isvc.HashCache.hash_file = staticmethod(lambda p: calls.append(p) or orig(p))
    try:
        assert cache.is_unchanged(str(f), ".txt") and calls == []     # same size+mtime: no read
        f.write_text("hello world")
        assert not cache.is_unchanged(str(f), ".txt") and calls == [str(f)]
    finally:
        isvc.HashCache.hash_file = staticmethod(orig)
