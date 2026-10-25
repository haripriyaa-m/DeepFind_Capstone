"""
pipeline.py — the glue the spec left out.

  HP watcher --Tier 1 docs--> search_engine.upsert
  HP watcher --Tier 2 queue--> (wait until system idle) --> vision_service
             --> map ocr_text->content, image_vector->vector --> search_engine.upsert

If vision_service can't load (missing models/deps) images are still indexed by
file name, but NOT marked as indexed, so they are retried on the next start.
"""

from __future__ import annotations

import importlib
import logging
import os
import queue
import threading
import time
from typing import Callable, Optional

import ingestion_service as isvc
import search_engine as se
from backend import ocr_engines

logger = logging.getLogger("deepfind.pipeline")

# How often files are read. "realtime" = as soon as they change (the default); the interval
# schedules read everything that changed since the last run; "manual" only on "Index now".
SCHEDULES: dict[str, Optional[int]] = {          # value = minutes between runs, None = no timer
    "realtime": None, "15m": 15, "30m": 30, "1h": 60, "6h": 360, "24h": 1440, "manual": None,
}
DEFAULT_SCHEDULE = "realtime"
SCHEDULE_TICK_SEC = 15
# File types whose text can differ with a different OCR engine
OCR_AFFECTED = set(isvc.IMAGE_EXTENSIONS) | {isvc.PDF_EXTENSION} | set(isvc.VIDEO_EXTENSIONS)


class Pipeline:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._vision = None
        self._vision_failed = False
        self.folders: list[str] = []
        self.tier2_processed = 0
        self.tier2_failed = 0
        self.tier2_current: Optional[str] = None
        self.ignored_files: list[str] = []
        # scheduled indexing
        self.schedule = DEFAULT_SCHEDULE
        self.last_index_run = 0.0                 # epoch seconds of the last run (0 = never)
        self.next_index_run: Optional[float] = None
        self.on_index_run: Optional[Callable[[float], None]] = None   # lets the API persist last_index_run

    # ---- lifecycle -----------------------------------------------------------
    def start(self, folders: list[str], cpu_limit: int = 50, ram_limit: int = 75,
              pause_on_battery: bool = True, index_schedule: str = DEFAULT_SCHEDULE,
              last_index_run: float = 0.0):
        self.ignored_files = []
        self.folders = list(folders)
        self.last_index_run = float(last_index_run or 0.0)
        self.schedule = index_schedule if index_schedule in SCHEDULES else DEFAULT_SCHEDULE
        isvc.register_appu_callback(self._route)
        isvc.update_limits(cpu_limit, ram_limit, pause_on_battery)
        isvc.set_deferred(self.schedule != "realtime")
        # realtime: read everything now. Scheduled/manual: only watch, the schedule decides.
        isvc.start_watcher(self.folders, scan=self.schedule == "realtime")
        self._plan_next_run(first=True)
        if not (self._thread and self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(target=self._tier2_loop, daemon=True, name="tier2-worker")
            self._thread.start()
            threading.Thread(target=self._schedule_loop, daemon=True, name="index-scheduler").start()

    def stop(self):
        self._stop.set()
        isvc.stop_watcher()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def apply_settings(self, folders: list[str], cpu_limit: int, ram_limit: int, pause_on_battery: bool,
                       index_schedule: Optional[str] = None, ocr_engine: Optional[str] = None):
        isvc.update_limits(cpu_limit, ram_limit, pause_on_battery)

        engine_changed = ocr_engine is not None and ocr_engine != ocr_engines.get_engine()
        if engine_changed:
            ocr_engines.set_engine(ocr_engine)

        if index_schedule is not None and index_schedule in SCHEDULES and index_schedule != self.schedule:
            self.set_schedule(index_schedule)

        if sorted(folders) != sorted(self.folders):
            for old in set(self.folders) - set(folders):   # folder removed -> forget its files
                se.delete(os.path.normpath(os.path.abspath(old)), recursive=True)
                isvc.purge_hash_prefix(os.path.normpath(os.path.abspath(old)))
            added = [os.path.normpath(os.path.abspath(f)) for f in set(folders) - set(self.folders)]
            self.folders = list(folders)
            isvc.start_watcher(self.folders, scan=self.schedule == "realtime")
            if added and self.schedule != "realtime":
                isvc.index_now(paths=added)     # choosing a folder is a request to read it

        if engine_changed:
            self.rescan_ocr_files()

    def models_changed(self):
        """New models on disk: retry vision and rescan, so images that were only indexed by
        name (hash never committed) go through OCR/CLIP now. Already-indexed files are skipped."""
        self._vision_failed = False
        isvc.start_watcher(self.folders)      # explicit user action: always rescan

    def reindex(self):
        isvc.reset_cache()
        isvc.start_watcher(self.folders)      # explicit user action: always rescan

    # ---- scheduled indexing --------------------------------------------------
    def set_schedule(self, schedule: str):
        """Switch between instant / every N minutes / manual."""
        if schedule not in SCHEDULES:
            raise ValueError(f"Unknown schedule: {schedule!r}")
        self.schedule = schedule
        isvc.set_deferred(schedule != "realtime")   # leaving a deferred mode reads what piled up
        self._plan_next_run()

    def index_now(self):
        """Read every change now (works in every schedule mode)."""
        self.last_index_run = time.time()
        self._plan_next_run()
        isvc.index_now()
        if self.on_index_run:
            try:
                self.on_index_run(self.last_index_run)
            except Exception:
                logger.exception("could not remember the last index run")

    def rescan_ocr_files(self):
        """The OCR engine changed: forget images and PDFs so they are read again with it."""
        n = isvc.purge_hash_extensions(OCR_AFFECTED)
        logger.info("OCR engine is now %s; re-reading %d image/PDF file(s)", ocr_engines.get_engine(), n)
        isvc.index_now(exts=OCR_AFFECTED)

    def _plan_next_run(self, first: bool = False):
        minutes = SCHEDULES.get(self.schedule)
        if minutes is None:                       # instant or manual: no timer
            self.next_index_run = None
        elif not self.last_index_run:             # never run: due right away
            self.next_index_run = time.time()
        else:                                     # a time in the past just means "overdue, run now"
            self.next_index_run = self.last_index_run + minutes * 60

    def _schedule_loop(self):
        while not self._stop.wait(SCHEDULE_TICK_SEC):
            due = self.next_index_run
            if due is not None and time.time() >= due and SCHEDULES.get(self.schedule):
                try:
                    self.index_now()
                except Exception:
                    logger.exception("scheduled index run failed")

    def status(self) -> dict:
        s = isvc.get_stats()
        s.update(tier2_processed=self.tier2_processed, tier2_failed=self.tier2_failed,
                 tier2_current=self.tier2_current, vision_available=not self._vision_failed,
                 index_schedule=self.schedule, last_index_run=self.last_index_run or None,
                 next_index_run=self.next_index_run, ocr_engine=ocr_engines.get_engine(),
                 ignored_files=self.ignored_files)
        return s

    # ---- Tier 1 / deletes -----------------------------------------------------
    def _route(self, doc: dict):
        if doc.get("deleted"):
            se.delete(doc["file_path"], recursive=bool(doc.get("recursive")))
            return
        se.upsert(doc)

    # ---- Tier 2 ---------------------------------------------------------------
    def _get_vision(self):
        if self._vision is None and not self._vision_failed:
            try:
                self._vision = importlib.import_module("vision_service")
            except Exception:
                logger.exception("vision_service unavailable — images will be indexed by name only")
                self._vision_failed = True
        return self._vision

    def _tier2_loop(self):
        q = isvc.get_tier2_queue()
        while not self._stop.is_set():
            try:
                item = q.get(timeout=1)
            except queue.Empty:
                continue
            while isvc.should_pause_tier2() and not self._stop.is_set():
                self._stop.wait(2)          # system busy / on battery: hold heavy work
            if self._stop.is_set():
                return
            try:
                self._process_tier2(item)
            except Exception:
                self.tier2_failed += 1
                logger.exception("Tier 2 failed for %s", item.get("file_path"))
            finally:
                self.tier2_current = None

    def _process_tier2(self, item: dict):
        path = item["file_path"]
        if not os.path.isfile(path) or not isvc.is_watched(path):
            return   # deleted, or its folder was removed from the watch list
        self.tier2_current = path
        doc = dict(item, needs_ocr=False)
        ok = False
        vision = self._get_vision()
        if vision is not None:
            result = vision.process_media_file(path)
            doc["content"] = result.get("ocr_text") or item.get("content", "")
            doc["vector"] = result.get("image_vector")
            segments = result.get("segments")
            if segments is not None:            # a video: its frames are searched, not one vector
                doc["segments"] = segments
            ok = bool(result.get("ocr_text")) or result.get("image_vector") is not None or bool(segments)
            
            if not ok:
                # The file was processed but yielded no text/vector (e.g. foreign language).
                # Ignore it and mark it as indexed so it doesn't perpetually clog the queue.
                doc["content"] = "This file is ignored for indexing."
                ok = True
                if path not in self.ignored_files:
                    self.ignored_files.append(path)
                    # keep list small
                    if len(self.ignored_files) > 100:
                        self.ignored_files.pop(0)

        if not os.path.isfile(path) or not isvc.is_watched(path):   # changed while we worked
            return
        se.upsert(doc)
        if ok:
            isvc.mark_indexed(path)
            self.tier2_processed += 1
        else:
            self.tier2_failed += 1          # left unmarked -> retried on next scan


pipeline = Pipeline()