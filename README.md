# DeepFind — Offline Semantic File Search (capstone)

Local, offline, CPU-only Windows app: hybrid keyword + AI search over your files.
React UI in a native WebView2 window · FastAPI · LanceDB · ONNX Runtime (no PyTorch).

## Modules
| Owner | File | Role |
|---|---|---|
| HP | `ingestion_service.py` | watcher, throttle monitor, text extraction, hash cache |
| Aaling | `vision_service.py` | OCR + CLIP image embeddings (ONNX) |
| Appu | `search_engine.py` | LanceDB storage + hybrid search (keyword strength + meaning strength) |
| shared | `video_frames.py` | picks a few representative frames per video (key frames + scene changes) |
| shared | `pipeline.py` | glue: Tier 1 -> DB, Tier 2 queue -> vision -> DB |
| Karthik | `backend/main.py`, `backend/models.py` | FastAPI gateway, model download |
| Karthik | `frontend/` | React UI ("Cobalt" design) |
| Karthik | `desktop/main.py`, `packaging/` | Windows app shell, launcher, installer |

## Install (Windows 10/11)
Run `DeepFind-Setup-0.4.0.exe` — per-user, no admin rights. SmartScreen may say "unknown publisher"
(the installer isn't code-signed): *More info → Run anyway*. Then **Folders → Add folder**, and
optionally **Settings → Download models** (~350 MB, once) to enable search by meaning and OCR.
Data lives in `%LOCALAPPDATA%\DeepFind`. A portable zip is also produced (unzip, run `DeepFind.exe`).

## Updates (Windows)
Installed copies check GitHub Releases (Settings → Updates; once a day if "Check automatically" is on) and
update in place. **To ship a new version:** change `VERSION` (e.g. `0.3.1`), add a `## 0.3.1 — …` section to
`CHANGES.md` (it becomes the release notes), then
```bash
git tag v0.3.1 && git push origin v0.3.1
```
GitHub Actions builds `DeepFind-Setup-0.3.1.exe`, its `.sha256` and the portable zip and publishes the release.
The repo must be **public** (the app asks GitHub without a login), and `update_source.json` must name it —
the workflow fills that in automatically; for a local build edit `"repo"` yourself
(`owner/name`; in an installed app it is `app\update_source.json`).

## Develop
```bash
pip install -r requirements.txt
python scripts/download_models.py     # once, with internet; copies go in ./models/
pytest tests                          # no models needed (encoder/vision are stubbed)

# UI with hot reload: backend on 8765, Vite proxies /api
python backend/main.py --port 8765
cd frontend && npm install && npm run dev      # http://localhost:1420

# the real desktop window (any OS with pywebview)
cd frontend && npm run build && cd .. && python desktop/main.py
```

## Build the Windows installer
From Linux, WSL or CI (`.github/workflows/windows-build.yml` does exactly this):
```bash
sudo apt install mingw-w64 nsis zip        # plus python3, node/npm, curl
bash packaging/build_windows.sh            # -> dist/DeepFind-Setup-0.4.0.exe, dist/DeepFind-0.4.0-portable.zip
WITH_MODELS=1 bash packaging/build_windows.sh   # also bundle ./models (bigger installer, no download step)
```
Layout of an install: `DeepFind.exe` (native launcher) · `runtime\` (CPython 3.11 + win_amd64
wheels) · `app\` (Python sources + `frontend\dist`).

## API (localhost)
`GET /api/search?q=&limit=` · `GET /api/status` · `GET|POST /api/settings` · `POST /api/reindex` ·
`POST /api/reveal` · `POST /api/open` · `GET /api/thumb?path=&size=` · `GET /api/library` ·
`GET /api/models` · `POST /api/models/download` · `GET /api/health` · `GET /` (the UI)
(`/api/*` returns 503 for a moment while the engine boots; poll `/api/health`.)

## Contracts
Ingestion payload -> `upsert()`: `{file_path, file_name, file_type, content, vector|None, modified_time, needs_ocr}`
Delete: `{"file_path", "deleted": True}` (+ `"recursive": True` for a folder).
Search result: `{file_name, file_path, file_type, snippet, score (0-1), match_type}`.

## Known limits
* Semantic search covers images (CLIP text->image). Text files are keyword-only (their vectors are zeros).
* Without models the app runs keyword-only (it says so in the UI) until they are downloaded.
* Needs the Microsoft Edge WebView2 runtime (built into Windows 11 / current Windows 10).

See `CHANGES.md` for the full change log.
