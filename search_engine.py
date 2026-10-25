"""
search_engine.py
Member 3 (Appu) — Embedded storage & hybrid search engine.

- Embedded LanceDB table (id, file_path, file_name, file_type, content, vector, modified_time)
- Lexical full-text search (LanceDB FTS) on content + file_name
- Query embedding via fastembed CLIP TEXT encoder Qdrant/clip-ViT-B-32-text
  (fp32 ONNX, ~250 MB) -> same 512-dim space as the vision team's image vectors
- Hybrid search: keyword + vector results fused with Reciprocal Rank Fusion (RRF)
- CRUD: init_db, upsert, delete (optionally recursive), exists, search, stats

Thread-safe: the watcher, the Tier-2 worker and the API can all call in.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import lancedb
import numpy as np
from lancedb.pydantic import LanceModel, Vector

logger = logging.getLogger("deepfind.search_engine")

VECTOR_DIM = 512
TABLE_NAME = "files"
CLIP_TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"
# Same folder download_models.py fills, so the offline machine never tries to download.
CLIP_CACHE_DIR = Path(os.environ.get("DEEPFIND_CLIP_CACHE_DIR", "./models/clip_cache"))
MIN_COSINE_SIMILARITY = 0.2   # vector hits below this are dropped before fusion
STRONG_COSINE_SIMILARITY = 0.30   # CLIP text->image similarity at/above this counts as a full "meaning" match
RRF_K = 60
OPTIMIZE_EVERY_N_UPSERTS = 500
IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "tiff", "tif", "bmp"}
VIDEO_TYPES = {"mp4", "mkv", "mov", "avi", "webm", "m4v"}
SEGMENTS_TABLE = "video_segments"     # one row per kept video frame: time, OCR text, CLIP vector

# Ranking. The order is decided by how STRONG each kind of evidence is, not only by list position:
#   keyword strength  = BM25 relative to the best keyword hit for this query (0-1)
#   meaning strength  = CLIP similarity scaled between MIN and STRONG (0-1)
# Words you typed count a bit more than a picture-meaning guess (CLIP is noisy on short queries,
# and text files have no meaning vector at all); a keyword hit that contains only some of the
# words you typed counts proportionally less. Rank fusion (RRF) is only a tie-break.
KEYWORD_WEIGHT = 1.0
SEMANTIC_WEIGHT = 0.8
FILENAME_BOOST = 0.05                  # a query word appears in the file name
RRF_TIEBREAK = 0.02
SHORT_QUERY_MIN_SIMILARITY = 0.24      # 1-2 word queries: weaker CLIP matches are mostly noise

_db = None
_table = None
_segments = None                    # video_segments table (None until init_db)
_text_encoder = None
_lock = threading.RLock()          # guards every table operation
_encoder_lock = threading.Lock()   # guards lazy model load
_upserts_since_optimize = 0
_encoder_error: Optional[str] = None   # set when the CLIP text model can't load -> keyword-only
_encoder_failed_at = 0.0
ENCODER_RETRY_SEC = 60.0               # don't re-attempt a failed model load on every keystroke


@dataclass
class SearchResult:
    file_name: str
    file_path: str
    file_type: str
    snippet: str
    score: float          # 0-1 strength the list is ordered by (best keyword hit == 1.0)
    match_type: str       # "semantic" | "keyword" | "hybrid"
    # --- ranking detail (all optional; shown in the UI) ---------------------------------
    rank: int = 0                         # 1-based position in this result list
    match_percent: int = 0                # 0-100 "how well it matches", never rises down the list
    keyword_rank: Optional[int] = None    # position in the keyword (BM25) list, if it was there
    keyword_score: Optional[float] = None # raw BM25 score from the keyword index
    semantic_rank: Optional[int] = None   # position in the meaning (vector) list, if it was there
    similarity: Optional[float] = None    # cosine similarity 0-1 between query and the file's vector
    start_sec: Optional[float] = None     # videos: where in the video the best match is

    def to_dict(self) -> dict:
        return asdict(self)


class FileRecord(LanceModel):
    id: str
    file_path: str
    file_name: str
    file_type: str
    content: str
    vector: Vector(VECTOR_DIM)  # type: ignore[valid-type]
    modified_time: float


def _schema(dim: int):
    """FileRecord with a vector of `dim` numbers (the image-search model decides the size)."""
    if dim == 512:
        return FileRecord
    from pydantic import create_model
    return create_model(f"FileRecord{dim}", __base__=LanceModel, id=(str, ...), file_path=(str, ...),
                        file_name=(str, ...), file_type=(str, ...), content=(str, ...),
                        vector=(Vector(dim), ...), modified_time=(float, ...))


def _segment_schema(dim: int):
    from pydantic import create_model
    return create_model(f"VideoSegment{dim}", __base__=LanceModel, id=(str, ...), file_path=(str, ...),
                        file_name=(str, ...), file_type=(str, ...), start_sec=(float, ...),
                        content=(str, ...), vector=(Vector(dim), ...))


def _table_dim(table) -> int:
    try:
        return int(table.schema.field("vector").type.list_size)
    except Exception:
        return VECTOR_DIM


def _model_dim() -> Optional[int]:
    from backend import model_store
    spec = model_store.image_spec()
    return spec["dim"] if spec else None


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

_encoder_key: Optional[str] = None
last_init_rebuilt = False             # init_db had to recreate the table (image model's vector size changed)


def _get_encoder():
    """Text half of the image-search model chosen in Settings -> Models."""
    global _text_encoder, _encoder_key
    from backend import model_store
    spec = model_store.image_spec()
    if spec is None:
        raise RuntimeError("Image search is switched off in Settings -> Models")
    key = f"{spec['text_repo']}|{spec['text_file']}"
    stale = lambda: _text_encoder is None or (_encoder_key is not None and key != _encoder_key)  # noqa: E731
    if stale():
        with _encoder_lock:
            if stale():
                from fastembed import TextEmbedding
                CLIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                model_store.register_fastembed(spec)
                _text_encoder = None
                _text_encoder = TextEmbedding(model_name=spec["text_repo"], cache_dir=str(CLIP_CACHE_DIR))
                _encoder_key = key
    return _text_encoder


def warm_up() -> None:
    """Load the text encoder now so the first query isn't the slow one."""
    _try_encoder()


def _try_encoder():
    """Encoder or None. A missing model/dependency degrades search to keyword-only
    instead of failing every query; the reason is exposed via semantic_status()."""
    global _encoder_error, _encoder_failed_at
    if _text_encoder is None and _encoder_error and time.time() - _encoder_failed_at < ENCODER_RETRY_SEC:
        return None
    try:
        enc = _get_encoder()
        _encoder_error = None
        return enc
    except Exception as e:
        if _encoder_error is None:
            logger.exception("CLIP text encoder unavailable — keyword-only search")
        _encoder_error = f"{type(e).__name__}: {e}"
        _encoder_failed_at = time.time()
        return None


def reset_encoder_error() -> None:
    """Models were just installed / switched: allow the next query to load the encoder immediately."""
    global _encoder_error, _encoder_failed_at, _text_encoder
    _encoder_error, _encoder_failed_at = None, 0.0
    from backend import model_store
    spec = model_store.image_spec()
    if _encoder_key is not None and (spec is None or f"{spec['text_repo']}|{spec['text_file']}" != _encoder_key):
        _text_encoder = None             # a different model (or none) is selected now


def semantic_status() -> dict:
    return {"semantic_available": _text_encoder is not None, "semantic_error": _encoder_error}


def init_db(db_path: str, preload_encoder: bool = True):
    """Initialize (or open) the local, serverless LanceDB instance."""
    global _db, _table, _segments
    with _lock:
        Path(db_path).mkdir(parents=True, exist_ok=True)
        _db = lancedb.connect(db_path)
        try:
            _table = _db.open_table(TABLE_NAME)      # replaces deprecated table_names()
        except Exception:
            _table = _db.create_table(TABLE_NAME, schema=_schema(_model_dim() or VECTOR_DIM), mode="create")
        _create_fts_indexes(_table)
        _set_dim(_table_dim(_table))
        _open_segments()
    global last_init_rebuilt
    last_init_rebuilt = ensure_vector_dim()
    if last_init_rebuilt:
        logger.info("vector size changed -> index rebuilt empty; files will be read again")
    if preload_encoder:
        _get_encoder()
    return _table


def _open_segments() -> None:
    """Open (or create) the video-frame table, matching the vector size of the files table."""
    global _segments
    try:
        _segments = _db.open_table(SEGMENTS_TABLE)
        if _table_dim(_segments) != VECTOR_DIM:
            _db.drop_table(SEGMENTS_TABLE)
            raise ValueError("vector size changed")
    except Exception:
        _segments = _db.create_table(SEGMENTS_TABLE, schema=_segment_schema(VECTOR_DIM), mode="create")
    _create_fts_indexes(_segments, cols=("content",))


def _set_dim(dim: int) -> None:
    global VECTOR_DIM
    VECTOR_DIM = dim


def ensure_vector_dim() -> bool:
    """If the chosen image-search model makes vectors of another size than the table holds,
    recreate the table empty (vectors of different models can't be compared). True = rebuilt;
    the caller then has every file read again."""
    global _table
    dim = _model_dim()
    with _lock:
        if _table is None or dim is None or dim == _table_dim(_table):
            return False
        _db.drop_table(TABLE_NAME)
        _table = _db.create_table(TABLE_NAME, schema=_schema(dim), mode="create")
        _create_fts_indexes(_table)
        _set_dim(dim)
        try:
            _db.drop_table(SEGMENTS_TABLE)
        except Exception:
            pass
        _open_segments()
        return True


def _create_fts_indexes(table, cols=("content", "file_name")) -> None:
    """(Re)build the keyword indexes. Uses create_index(config=FTS()) on current LanceDB;
    falls back to the older create_fts_index() so old installs keep working."""
    try:
        from lancedb.index import FTS
        for col in cols:
            table.create_index(col, config=FTS(), replace=True)
    except ImportError:
        try:
            for col in cols:
                table.create_fts_index(col, replace=True)
        except Exception:
            logger.exception("FTS index creation failed (keyword search unavailable)")
    except Exception:
        logger.exception("FTS index creation failed (keyword search unavailable)")


def _ensure_ready():
    if _table is None:
        raise RuntimeError("Call init_db(db_path) before using the search engine.")


def _esc(s: str) -> str:
    return s.replace("'", "''")


def _file_id(path: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, path))


def _clean_vector(vector) -> list:
    """None / wrong-size / NaN vectors become zero vectors (never match semantically)."""
    if vector is None:
        return [0.0] * VECTOR_DIM
    try:
        arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    except Exception:
        logger.warning("Unusable vector, storing zeros")
        return [0.0] * VECTOR_DIM
    if arr.shape[0] != VECTOR_DIM or not np.isfinite(arr).all():
        logger.warning("Bad vector (len=%s), storing zeros", arr.shape[0])
        return [0.0] * VECTOR_DIM
    return arr.tolist()


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def upsert(doc_dict: dict):
    """Add a new file or replace the existing record (matched on file_path)."""
    global _upserts_since_optimize
    _ensure_ready()
    path = doc_dict["file_path"]
    record = {
        "id": _file_id(path),
        "file_path": path,
        "file_name": doc_dict.get("file_name") or Path(path).name,
        "file_type": doc_dict.get("file_type") or Path(path).suffix.lstrip(".").lower(),
        "content": doc_dict.get("content") or "",
        "vector": _clean_vector(doc_dict.get("vector")),
        "modified_time": float(doc_dict.get("modified_time", time.time())),
    }
    segs = doc_dict.get("segments")
    with _lock:
        _table.delete(f"file_path = '{_esc(path)}'")
        _table.add([record])
        if _segments is not None and (segs is not None or record["file_type"] in VIDEO_TYPES):
            _segments.delete(f"file_path = '{_esc(path)}'")       # a video: its frames are replaced too
            rows = []
            for sg in segs or []:
                vec = _clean_vector(sg.get("vector"))
                if not any(vec) and not sg.get("text"):
                    continue          # nothing to find this frame by
                start = float(sg.get("start", 0.0))
                rows.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}#{start:.2f}")),
                             "file_path": path, "file_name": record["file_name"],
                             "file_type": record["file_type"], "start_sec": start,
                             "content": sg.get("text") or "", "vector": vec})
            if rows:
                _segments.add(rows)
        _upserts_since_optimize += 1
        if _upserts_since_optimize >= OPTIMIZE_EVERY_N_UPSERTS:
            _upserts_since_optimize = 0
            try:
                _table.optimize()   # compacts fragments + folds new rows into the FTS index
            except Exception:
                logger.exception("table.optimize() failed")


# Alias names used in the project spec
upsert_document = upsert


def delete(file_path: str, recursive: bool = False):
    """Remove a record. recursive=True removes everything under a folder path."""
    _ensure_ready()
    with _lock:
        if recursive:
            prefix = file_path.rstrip("/\\") + os.sep
            _table.delete(f"starts_with(file_path, '{_esc(prefix)}')")
            if _segments is not None:
                _segments.delete(f"starts_with(file_path, '{_esc(prefix)}')")
        else:
            _table.delete(f"file_path = '{_esc(file_path)}'")
            if _segments is not None:
                _segments.delete(f"file_path = '{_esc(file_path)}'")


delete_document = delete


def exists(file_path: str) -> bool:
    _ensure_ready()
    with _lock:
        return _table.count_rows(f"file_path = '{_esc(file_path)}'") > 0


def count() -> int:
    _ensure_ready()
    with _lock:
        return _table.count_rows()


def count_under(folder: str) -> int:
    """Rows whose file_path lives under folder (recursive)."""
    _ensure_ready()
    prefix = folder.rstrip("/\\") + os.sep
    with _lock:
        return _table.count_rows(f"starts_with(file_path, '{_esc(prefix)}')")


def type_counts() -> dict:
    """{file_type: n} over the whole index. Reads only the file_type column (no vectors)."""
    _ensure_ready()
    with _lock:
        try:
            col = _table.search().select(["file_type"]).limit(None).to_arrow().column("file_type")
        except Exception:
            logger.exception("type_counts failed")
            return {}
    return {d["values"] or "other": int(d["counts"]) for d in col.value_counts().to_pylist()}


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _rrf_fuse(keyword_ids: list, vector_ids: list, k: int = RRF_K) -> dict:
    scores: dict = {}
    for rank, _id in enumerate(keyword_ids):
        scores[_id] = scores.get(_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, _id in enumerate(vector_ids):
        scores[_id] = scores.get(_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _snippet(content: str, query: str = "", file_type: str = "", length: int = 160) -> str:
    if not content:
        return "[Visual match]" if (file_type in IMAGE_TYPES or file_type in VIDEO_TYPES) else ""
    flat = " ".join(content[:200_000].split())
    start = 0
    low = flat.lower()
    hits = [low.find(t) for t in re.findall(r"\w+", query.lower()) if len(t) >= 3]
    hits = [h for h in hits if h >= 0]
    if hits:
        start = max(0, min(hits) - length // 4)
    seg = flat[start:start + length]
    return ("..." if start > 0 else "") + seg + ("..." if start + length < len(flat) else "")


def _video_frame_hits(query_vector, limit: int, min_similarity: float) -> list:
    """Best-matching frame of each video, shaped like files-table vector hits (plus `_start`)."""
    if _segments is None:
        return []
    with _lock:
        try:
            raw = _segments.search(query_vector).metric("cosine").limit(limit * 6).to_list()
        except Exception:
            logger.exception("video frame search failed")
            return []
    best: dict = {}
    for r in raw:
        d = r.get("_distance")
        if d is None or not np.isfinite(d) or (1.0 - d) < min_similarity:
            continue
        cur = best.get(r["file_path"])
        if cur is None or d < cur["_distance"]:
            best[r["file_path"]] = r
    return [{"id": _file_id(r["file_path"]), "file_path": r["file_path"], "file_name": r["file_name"],
             "file_type": r["file_type"], "content": r.get("content") or "",
             "_distance": r["_distance"], "_start": r["start_sec"]} for r in best.values()]


def _video_keyword_start(path: str, query: str) -> Optional[float]:
    """Where in a video (found by words) the words appear: the best-matching frame's OCR text."""
    if _segments is None:
        return None
    with _lock:
        try:
            rows = (_segments.search(query, query_type="fts")
                    .where(f"file_path = '{_esc(path)}'", prefilter=True).limit(1).to_list())
        except Exception:
            return None
    return float(rows[0]["start_sec"]) if rows else None


def search(query_str: str, limit: int = 20, min_similarity: float = MIN_COSINE_SIMILARITY) -> list:
    """Keyword (FTS) + meaning (CLIP vectors of images and video frames), ranked by strength."""
    _ensure_ready()
    query_str = (query_str or "").strip()
    if not query_str:
        return []

    words = [t for t in re.findall(r"\w+", query_str.lower()) if len(t) >= 2] or re.findall(r"\w+", query_str.lower())
    tokens = [t for t in words if len(t) >= 3]
    if min_similarity == MIN_COSINE_SIMILARITY and len(re.findall(r"\w+", query_str)) <= 2:
        min_similarity = SHORT_QUERY_MIN_SIMILARITY

    with _lock:
        try:
            kw_results = _table.search(query_str, query_type="fts").limit(limit * 2).to_list()
        except Exception:
            logger.exception("FTS failed, falling back to vector-only results")
            kw_results = []

    raw_vec = []
    encoder = _try_encoder()
    query_vector = None
    if encoder is not None:
        query_vector = next(iter(encoder.embed([query_str]))).tolist()
        with _lock:
            raw_vec = _table.search(query_vector).metric("cosine").limit(limit * 2).to_list()

    # LanceDB cosine "_distance" = 1 - cosine_similarity. Zero vectors give NaN -> dropped.
    vec_results = []
    for r in raw_vec:
        d = r.get("_distance")
        if d is not None and np.isfinite(d) and (1.0 - d) >= min_similarity:
            vec_results.append(r)
    if query_vector is not None:                      # videos: their best frame competes with images
        vec_results += _video_frame_hits(query_vector, limit, min_similarity)
        vec_results.sort(key=lambda r: r["_distance"])
        vec_results = vec_results[:limit * 2]

    kw_ids = [r["id"] for r in kw_results]
    vec_ids = [r["id"] for r in vec_results]
    fused = _rrf_fuse(kw_ids, vec_ids)
    # a #1 hit in both lists == 1.0; keyword-only mode normalises against one list
    rrf_max = (2.0 if encoder is not None else 1.0) / (RRF_K + 1)

    by_id = {r["id"]: r for r in vec_results}
    by_id.update({r["id"]: r for r in kw_results})        # keyword row last: it holds the whole text
    kw_set, vec_set = set(kw_ids), set(vec_ids)
    kw_rank = {_id: i + 1 for i, _id in enumerate(kw_ids)}
    vec_rank = {_id: i + 1 for i, _id in enumerate(vec_ids)}
    kw_row = {r["id"]: r for r in kw_results}
    vec_row = {r["id"]: r for r in vec_results}
    best_bm25 = max((float(r.get("_score") or 0.0) for r in kw_results), default=0.0)

    scored = []
    for _id in fused:
        row = by_id[_id]
        in_kw, in_vec = _id in kw_set, _id in vec_set
        bm25 = float(kw_row[_id].get("_score") or 0.0) if in_kw else None
        sim = (1.0 - float(vec_row[_id]["_distance"])) if in_vec else None

        # Two 0-1 parts: keyword strength (relative to the best keyword hit for this query)
        # and meaning strength (cosine similarity scaled so STRONG_COSINE_SIMILARITY == full).
        kw_part = 0.0
        if in_kw:
            kw_part = (bm25 / best_bm25) if (bm25 and best_bm25) else 1.0 / kw_rank[_id]
        sem_part = 0.0
        if in_vec:
            sem_part = (sim - min_similarity) / max(1e-6, STRONG_COSINE_SIMILARITY - min_similarity)
        if in_kw and len(words) > 1:           # how many of the typed words does this file contain?
            hay = ((row["content"] or "")[:200_000] + " " + row["file_name"]).lower()
            kw_part *= sum(1 for w in words if w in hay) / len(words)
        kw_part, sem_part = min(max(kw_part, 0.0), 1.0), min(max(sem_part, 0.0), 1.0)

        # what the list is ordered by: weighted evidence (either kind counts, both count more)
        strength = 1.0 - (1.0 - KEYWORD_WEIGHT * kw_part) * (1.0 - SEMANTIC_WEIGHT * sem_part)
        name = row["file_name"].lower()
        if tokens and any(t in name for t in tokens):
            strength += FILENAME_BOOST
        strength = min(strength, 1.0)
        order = strength + RRF_TIEBREAK * min(fused[_id] / rrf_max, 1.0)
        # what is shown: the same evidence, unweighted ("how well does it match?")
        shown = 1.0 - (1.0 - kw_part) * (1.0 - sem_part)
        scored.append((order, _id, strength, shown, bm25, sim))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    ceiling = 100
    for pos, (_, _id, strength, shown, bm25, sim) in enumerate(scored[:limit], start=1):
        row = by_id[_id]
        in_kw, in_vec = _id in kw_set, _id in vec_set
        pct = max(1, min(round(100 * shown), ceiling))
        ceiling = pct            # list order is the ranking, so the percentage never goes up the list

        start = vec_row[_id].get("_start") if in_vec else None
        if start is None and row["file_type"] in VIDEO_TYPES and in_kw:
            start = _video_keyword_start(row["file_path"], query_str)

        results.append(SearchResult(
            file_name=row["file_name"],
            file_path=row["file_path"],
            file_type=row["file_type"],
            snippet=_snippet(row["content"], query_str, row["file_type"]),
            score=round(strength, 4),
            match_type="hybrid" if (in_kw and in_vec) else ("keyword" if in_kw else "semantic"),
            rank=pos,
            match_percent=pct,
            keyword_rank=kw_rank.get(_id),
            keyword_score=round(bm25, 3) if bm25 is not None else None,
            semantic_rank=vec_rank.get(_id),
            similarity=round(sim, 3) if sim is not None else None,
            start_sec=round(float(start), 1) if start is not None else None,
        ))
    return results
