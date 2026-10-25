"""Manual demo: watch a folder, index it, then search from the terminal.
    python demo_integration.py  (needs models downloaded: python scripts/download_models.py)
Automated equivalents live in tests/ (run: pytest tests)."""
import os
import time

from pipeline import pipeline
import search_engine as se

folder = os.path.abspath("./test_watch_folder")
os.makedirs(folder, exist_ok=True)
se.init_db("./test_lancedb")
pipeline.start([folder], cpu_limit=80, ram_limit=85, pause_on_battery=False)
print(f"Indexing {folder} — drop files in, then Ctrl+C to search.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pipeline.stop()
    q = input("\nSearch query: ")
    for r in se.search(q):
        print(r)
