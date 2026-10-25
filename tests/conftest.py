import hashlib
import os
import sys
import tempfile
from pathlib import Path

# Must be set BEFORE importing project modules (they read these at import time)
_DATA = tempfile.mkdtemp(prefix="deepfind_data_")
os.environ["DEEPFIND_DATA_DIR"] = _DATA
os.environ["DEEPFIND_DB_PATH"] = os.path.join(_DATA, "lancedb")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest


def fake_vec(text: str) -> np.ndarray:
    """Deterministic stand-in for a CLIP embedding (same text -> same unit vector)."""
    r = np.random.RandomState(int(hashlib.md5(text.encode()).hexdigest()[:8], 16))
    v = r.randn(512)
    return v / np.linalg.norm(v)


class FakeEncoder:
    def embed(self, texts):
        for t in texts:
            yield fake_vec(t)


@pytest.fixture()
def db(tmp_path):
    import search_engine as se
    se._text_encoder = FakeEncoder()
    se.init_db(str(tmp_path / "db"), preload_encoder=False)
    return se
