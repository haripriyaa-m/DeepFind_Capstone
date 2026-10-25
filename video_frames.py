"""
video_frames.py — pick a handful of representative frames from a video. No models, no ffmpeg binary:
PyAV (which carries its own ffmpeg libraries) does the decoding.

Indexing every frame would cost hours per video, so this is deliberately cheap:

  1. decode ONLY the codec's key frames (I-frames; the decoder skips everything else, so a 1-hour
     video costs seconds, not minutes);
  2. keep a key frame when the picture changed a lot since the last kept one (a new scene / slide:
     mean difference of 32x32 grey thumbnails), but never two frames closer than MIN_GAP_SEC;
  3. never leave more than MAX_GAP_SEC without a frame (talking heads, slow pans): such gaps are
     filled by seeking, which also covers videos that have almost no key frames;
  4. at most MAX_FRAMES frames per video (evenly thinned).

Typical result: a lecture of one hour -> 40-100 frames, a 3-minute clip -> 5-20 frames.
Frames come back as small JPEGs (a few MB for the whole video), so nothing big stays in memory.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("deepfind.video_frames")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}

MIN_GAP_SEC = 0.5        # never keep two frames closer than this
MAX_GAP_SEC = 15.0       # never go longer than this without a frame
SCENE_DIFF = 8.0         # mean |difference| (0-255) of 32x32 grey thumbnails = "new scene"
MAX_FRAMES = 300         # cap per video
SCAN_BUDGET_SEC = 120.0  # key-frame scan stops after this long; seeking fills the rest
FRAME_MAX_SIDE = 640     # kept frames are shrunk to this (CLIP looks at 224px; OCR wants more)
TEXT_EDGE_LEVEL = 40     # horizontal brightness step that counts as an "edge" (text has many)
SEEK_DECODE_LIMIT = 900  # frames decoded after a seek while looking for the wanted time


@dataclass
class Frame:
    time: float                  # seconds from the start
    jpeg: bytes                  # the frame, shrunk, as JPEG
    text_score: float = 0.0      # 0-1: share of strong horizontal edges (slides/screens score high)


@dataclass
class VideoSample:
    duration: float = 0.0
    frames: list = field(default_factory=list)


def _signature(frame) -> np.ndarray:
    return frame.reformat(width=32, height=32, format="gray").to_ndarray().astype(np.int16)


def _text_score(frame) -> float:
    g = frame.reformat(width=320, height=180, format="gray").to_ndarray().astype(np.int16)
    return float((np.abs(np.diff(g, axis=1)) > TEXT_EDGE_LEVEL).mean())


def _jpeg(frame) -> bytes:
    img = frame.to_image().convert("RGB")
    img.thumbnail((FRAME_MAX_SIDE, FRAME_MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _make(frame, t: float) -> Frame:
    return Frame(time=round(t, 2), jpeg=_jpeg(frame), text_score=_text_score(frame))


def _frame_time(frame, tb: float) -> Optional[float]:
    if frame.time is not None:
        return float(frame.time)
    if frame.pts is not None:
        return float(frame.pts) * tb
    return None


def _frame_at(container, stream, wanted: float, tb: float):
    """The frame at (or just after) `wanted` seconds, found by seeking. None if there isn't one."""
    container.seek(int(wanted / tb), stream=stream, backward=True, any_frame=False)
    last = None
    for n, frame in enumerate(container.decode(stream)):
        t = _frame_time(frame, tb)
        last = (frame, t if t is not None else wanted)
        if t is not None and t >= wanted - 0.05:
            break
        if n >= SEEK_DECODE_LIMIT:
            break
    return last


def extract_keyframes(path: str, max_frames: int = MAX_FRAMES,
                      scan_budget: float = SCAN_BUDGET_SEC) -> VideoSample:
    """Representative frames of one video (see the module notes). Raises on unreadable files."""
    import av

    out = VideoSample()
    with av.open(str(path)) as c:
        if not c.streams.video:
            return out
        vs = c.streams.video[0]
        tb = float(vs.time_base) if vs.time_base else 0.0
        if c.duration:
            out.duration = float(c.duration) / av.time_base
        elif vs.duration and tb:
            out.duration = float(vs.duration) * tb

        kept: list[Frame] = []
        last_sig: Optional[np.ndarray] = None
        last_t = -1e9

        # ---- 1+2: key frames, kept when the scene changed -------------------------------------
        vs.codec_context.skip_frame = "NONKEY"
        started = time.time()
        for frame in c.decode(vs):
            t = _frame_time(frame, tb)
            if t is None or t < 0:
                continue
            if t - last_t >= MIN_GAP_SEC:
                sig = _signature(frame)
                if last_sig is None or t - last_t >= MAX_GAP_SEC or \
                        float(np.abs(sig - last_sig).mean()) > SCENE_DIFF:
                    kept.append(_make(frame, t))
                    last_sig, last_t = sig, t
            if time.time() - started > scan_budget:        # pathological file: seeking takes over
                logger.info("key-frame scan of %s stopped after %.0fs", path, scan_budget)
                break
        # ---- 3: fill gaps longer than MAX_GAP_SEC by seeking ----------------------------------
        times = sorted(f.time for f in kept)
        end = out.duration if out.duration > 0 else (times[-1] if times else 0.0)
        wanted: list[float] = []
        if not times or times[0] >= MIN_GAP_SEC:
            wanted.append(0.0)                              # nothing near the start
        bounds = ([0.0] if wanted else []) + times + ([end] if end > (times[-1] if times else 0.0) else [])
        for a, b in zip(bounds, bounds[1:]):
            t = a + MAX_GAP_SEC
            while t < b - MIN_GAP_SEC and len(kept) + len(wanted) < max_frames * 2:
                wanted.append(t)
                t += MAX_GAP_SEC
        if wanted:
            vs.codec_context.skip_frame = "DEFAULT"
            for w in wanted:
                try:
                    got = _frame_at(c, vs, w, tb)
                except Exception:
                    logger.debug("seek to %.1fs failed", w, exc_info=True)
                    continue
                if got is not None:
                    kept.append(_make(got[0], got[1]))

    # ---- 4: cap -----------------------------------------------------------------------------
    kept.sort(key=lambda f: f.time)
    dedup: list[Frame] = []
    for f in kept:                                       # a seek can land on an already-kept frame
        if not dedup or f.time - dedup[-1].time >= 0.5:
            dedup.append(f)
    if len(dedup) > max_frames:
        idx = sorted(set(np.linspace(0, len(dedup) - 1, max_frames).round().astype(int).tolist()))
        dedup = [dedup[i] for i in idx]
    out.frames = dedup
    return out
