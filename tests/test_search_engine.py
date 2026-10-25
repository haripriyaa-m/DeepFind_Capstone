from conftest import fake_vec


def doc(path, content="", vector=None, ftype="txt"):
    return {"file_path": path, "file_name": path.split("/")[-1], "file_type": ftype,
            "content": content, "vector": vector, "modified_time": 1.0}


def test_keyword_search_and_snippet(db):
    db.upsert(doc("/f/resume.pdf", "Experienced data analyst skilled in Python and SQL", ftype="pdf"))
    db.upsert(doc("/f/notes.txt", "Grocery list: milk, eggs, bread"))
    r = db.search("Python")
    assert [x.file_name for x in r] == ["resume.pdf"] and r[0].match_type == "keyword"
    assert "Python" in r[0].snippet and 0 < r[0].score <= 1


def test_filename_search(db):
    db.upsert(doc("/f/quarterly_budget.xlsx", "", ftype="xlsx"))
    assert [x.file_name for x in db.search("quarterly_budget")] == ["quarterly_budget.xlsx"] or \
           [x.file_name for x in db.search("budget")] == ["quarterly_budget.xlsx"]


def test_semantic_image_match_via_vector(db):
    # an "image" whose vector equals the embedding of the query text => CLIP-style hit
    db.upsert(doc("/f/board.png", "", vector=fake_vec("whiteboard with architecture diagrams").tolist(), ftype="png"))
    db.upsert(doc("/f/other.png", "", vector=fake_vec("a cat on a sofa").tolist(), ftype="png"))
    r = db.search("whiteboard with architecture diagrams")
    assert r[0].file_name == "board.png" and r[0].match_type == "semantic"
    assert r[0].snippet == "[Visual match]"
    assert all(x.file_name != "other.png" for x in r)   # below similarity floor


def test_zero_vector_docs_never_semantic(db):
    db.upsert(doc("/f/a.txt", "hello world"))
    assert db.search("zzzz qqqq") == []


def test_update_replaces_row_and_delete(db):
    db.upsert(doc("/f/a.txt", "alpha zebra"))
    db.upsert(doc("/f/a.txt", "beta giraffe"))
    assert db.count() == 1
    assert db.search("zebra") == [] and db.search("giraffe")
    db.delete("/f/a.txt")
    assert db.count() == 0 and not db.exists("/f/a.txt")


def test_recursive_delete_only_hits_that_folder(db):
    import os
    s = os.sep
    for p in [f"{s}r{s}d{s}a.txt", f"{s}r{s}d{s}sub{s}b.txt", f"{s}r{s}dd{s}c.txt", f"{s}r{s}d_x{s}e.txt"]:
        db.upsert(doc(p, "x"))
    db.delete(f"{s}r{s}d", recursive=True)
    assert db.count() == 2 and db.exists(f"{s}r{s}dd{s}c.txt") and db.exists(f"{s}r{s}d_x{s}e.txt")


def test_quotes_in_paths_and_bad_vectors(db):
    db.upsert(doc("/f/it's.txt", "quoted", vector=[1.0, 2.0]))   # wrong length -> zeros, no crash
    assert db.exists("/f/it's.txt")
    db.delete("/f/it's.txt")
    assert db.count() == 0


def test_empty_query(db):
    assert db.search("   ") == []


def test_keyword_only_when_encoder_missing(db, monkeypatch):
    """A missing CLIP model must not break search — it degrades to keyword-only."""
    def boom():
        raise FileNotFoundError("models/clip_cache missing")
    monkeypatch.setattr(db, "_get_encoder", boom)
    db.upsert({"file_path": "/d/budget.txt", "content": "quarterly budget for the zebra project"})
    hits = db.search("zebra")
    assert [h.file_name for h in hits] == ["budget.txt"]
    assert hits[0].match_type == "keyword" and hits[0].score == 1.0
    st = db.semantic_status()
    assert st["semantic_error"] and "clip_cache" in st["semantic_error"]
