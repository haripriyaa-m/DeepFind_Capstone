# Changelog

## 0.4.0 — video search, fairer ranking, choose the update repository in the app

### Videos (mp4, mkv, mov, avi, webm, m4v)
- Videos are searchable by **what they show** and by **text on screen** (slides, screen recordings). A video is never
  indexed frame by frame: only the codec's key frames are decoded, one is kept per scene change (never closer than 2 s,
  never further apart than 30 s, at most 150 per video). A one-hour lecture is typically 40-100 frames.
- Each kept frame gets a CLIP vector (same model as images); frames that look like text are also read with OCR
  (at most 24 per video). Results show **"Matched at 12:34"** and a preview of that exact frame.
- A video is findable by file name straight away; its frames are read later, while the PC is idle.
  Videos are tracked by size + modified time, so multi-GB files are never read in full to check for changes.
- New dependency: `av` (PyAV, carries its own ffmpeg). New file: `video_frames.py`. No speech-to-text yet.

### Ranking
- Results are now ordered by **how strong** each match is, not only by position in the keyword and meaning lists
  (before, a weak #1 picture guess beat a file that mentions your word many times). Keyword strength is BM25 relative to
  the best hit and counts for the share of your words the file contains; meaning strength is CLIP similarity.
  Typed words weigh 1.0, meaning 0.8; a word in the file name adds a little. Rank fusion is only a tie-break.
- One- and two-word queries ignore weak picture matches (similarity below 0.24).

### Updates
- **Settings → Updates → Update source**: type `owner/name` or paste a GitHub link, press Save; DeepFind checks it
  right away. "Reset" returns to the repository built into the app. Stored per PC.

### Fixes
- "Index now", scheduled runs, choosing a folder in manual mode and changing the OCR engine now walk the folders, so
  files that appeared or changed while the app was closed are read (before, only watcher events were).
  Repeated scans skip unchanged files by size + time without re-reading them.

## 0.3.0 — follow-up 2: Models page, Settings sections, private-repo updates (2026-10-06)

### Settings → Models (new). Nothing downloads until you tick it.
- One page per job:
  - **Text inside images & scanned PDFs**: RapidOCR English, the Tesseract 5 engine, and 19 Tesseract language packs
    (English, Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Marathi, Gujarati, Punjabi, Urdu, Sanskrit, Arabic,
    Chinese, Japanese, Korean, French, German, Spanish).
  - **Photos by what they show**: CLIP ViT-B/32 and Jina CLIP v1.
- Each model card shows the size, speed and accuracy bars, its languages, the vector size, and what it is good and weak at.
  Tick models and press **Download selected**. They download one after another with progress, and you can cancel one
  that is waiting. **Remove download** frees disk space. A language pack automatically queues the Tesseract engine first.
- **In use**: choose which downloaded model does each job, or turn a job off. For Tesseract you also tick which
  languages to read (they are combined, e.g. `eng+hin`).
  - When you change the OCR model, images and scans are read again.
  - When you change the image model, pictures are encoded again. If the new model's vector size is different
    (e.g. 512 → 768), the index is rebuilt.
- **Add your own model**: give it a name and a type, and it is listed for its job:
  - *Tesseract language pack*: a language code, plus an optional link (e.g. a tessdata_best file).
  - *RapidOCR / PaddleOCR model files*: links to the detection model, the recognition model and the character list.
  - *Image-search model from Hugging Face*: a CLIP-style ONNX model in fastembed layout (repository, text/image file,
    vector size). After the download it is checked by encoding a sentence and a picture.
- **Tesseract installs inside the app now**, with no administrator rights and no Windows prompt. DeepFind downloads the
  official installer and unpacks only `tesseract.exe` and its DLLs into `models\tesseract`, using the official 7-Zip
  build fetched just for that step. The installer itself is never run. A system-wide Tesseract is still detected.
- Backend:
  - `backend/model_store.py`: the catalogue, custom models, download queue and active selection.
  - `scripts/fetch_model.py`: downloads and checks image models.
  - API: `GET /api/catalog` and `POST /api/catalog/{download,cancel,remove,custom,custom/delete,active}`.
  - Settings gain `ocr_model`, `ocr_languages` and `image_model`.
  - `vision_service` / `search_engine` now load whichever model is active, and the LanceDB vector size follows the model.
- Tests: `tests/test_model_store.py` (5 tests).

### Settings layout
- Settings opens as a **list of sections**: Indexing · Models · Performance · Updates · Privacy & about.
  Each shows its current state. Clicking a section opens its own page with all of its options and a **‹ Settings** back link.
- The former "Text recognition (OCR)" and "AI models" cards are merged into Models. The other options are unchanged.

### Updates
- **Private GitHub repositories**: add a read-only token once per PC under Settings → Updates. It is stored only in
  `%LOCALAPPDATA%\DeepFind\updates\github_token`, used only for `api.github.com`, and never forwarded to the download
  redirect. Releases and assets are then fetched through the API.
- Updates no longer re-download libraries that haven't changed:
  - the full installer's build now installs exactly the pinned `requirements-windows.lock.txt` and records it as
    `runtime\.deepfind-libs.lock`;
  - the small online installer (which in-app updates use) skips pip when the lock file is the same;
  - when the lock has changed and pip is missing (full-installer copies), it fetches pip first.
- The Updates page shows the configured repository. `update_source.json` explains where to put the repo name.

## 0.3.0 — follow-up: install/update kit + fixes merged back (2026-10-06)

### Added
- **`install-deepfind.sh`** (in `packaging/windows/`, shipped in the kit): installs DeepFind as a desktop app on
  any 64-bit Windows 10/11 laptop from **Git Bash or WSL**. It copies the app to `%LOCALAPPDATA%\Programs\DeepFind`,
  downloads the Python runtime and pinned libraries, creates Desktop + Start-menu shortcuts, registers the app in
  *Settings → Apps* (with `Uninstall DeepFind.cmd`), and writes `HKCU\Software\DeepFind\InstallDir` so in-app
  updates recognise this copy. Options: `--no-launch`, `--no-desktop`, `--dir`, `--uninstall`.
- **Incremental updates.** Run a newer kit on a laptop that already has DeepFind and the script switches to
  *update mode*: it byte-compares and replaces only changed app files, deletes removed ones, reuses `runtime\`,
  and skips pip completely when `requirements-windows.lock.txt` is unchanged (it keeps a copy as
  `runtime\.deepfind-libs.lock`). When the lock changes, pip downloads only the packages whose version changed.
- **`packaging/make_kit.sh`** → `dist/DeepFind-<ver>-kit.zip` (~5 MB): script + online installer + app payload +
  README. `build_windows.sh` runs it at the end. The same zip is used for first installs and for updates.
- The **online installer** (`DeepFind-Setup-<ver>-online.exe`, ~3 MB) is built again. It had been dropped in 0.3.0.
  It works with `/UPDATE` too.
- **In-app updater** prefers the `-online.exe` release asset. An installed copy already has the runtime and libraries,
  so an update downloads about 3 MB instead of about 180 MB. The full installer is the fallback.
  Test: `test_prefers_small_online_installer`.

### Fixed (0.2.1 fixes that 0.3.0 had lost — 0.3.0 had branched from 0.2.0)
- `desktop/main.py`: the app could freeze ("Not responding") because 0.3.0 set the .NET window icon from a Python thread
  again. It now finds the window with plain Win32 calls through ctypes.
- `desktop/main.py`: the loading screen, the readable error page and the WebView2 check are back. Merged with 0.3.0's
  `VERSION`-file version, the `open_url` bridge and the updater quit hooks. The colours now follow the light theme.
- `packaging/windows/requirements-windows.lock.txt` is restored (69 pinned packages). The online installer and the script need it.

## 0.3.0 — Updates inside the app (2026-10-06)
- **Settings → Updates**: check for a new version, download it, and install it with one click. Your
  index, folders and settings are kept; DeepFind closes, updates and reopens by itself.
- Checks automatically once a day (one request to github.com, nothing about your files) — switch it off
  in Settings if you want DeepFind fully offline.
- The Settings tab shows a dot when an update is waiting; the first launch after an update says so.
- Downloads are verified (SHA-256) before the installer is started.

### Technical
- `backend/updater.py` + `GET /api/update`, `POST /api/update/{check,download,install,ack}`; releases come from
  GitHub Releases of the repo named in `update_source.json` (falls back to the github.com web redirect when
  the API is rate-limited). `DEEPFIND_UPDATE_URL` can point at a JSON manifest instead.
- The installer understands `/UPDATE`: no welcome/folder/finish pages, waits (via the app's single-instance
  mutex) until the old process has exited, then starts the new version.
- One version source: the `VERSION` file (build script, app, API and installer all read it).
- `.github/workflows/windows-build.yml`: pushing a tag `vX.Y.Z` builds the installer, writes the SHA-256 file
  and publishes the GitHub Release that installed apps look for.
- Portable zip / running from source: the app reports updates but sends you to the download page instead of
  replacing itself.

## 0.2.0 — Windows desktop app + frontend (2026-09-30)

### Added — desktop app (Windows)
- **`desktop/main.py`** — the app shell. One process: starts the FastAPI engine on a free
  `127.0.0.1` port in a thread, opens a native window (pywebview → Microsoft Edge **WebView2**)
  on the UI, exposes a small JS bridge (`pick_folders`, `confirm`, `app_info`, `open_path`),
  stops the engine cleanly when the window closes.
  - user data in `%LOCALAPPDATA%\DeepFind\{data,models,logs}` (install folder stays read-only)
  - single-instance lock (named mutex) — a 2nd launch focuses the running window instead of opening
    a second engine on the same index
  - dark themed Windows title bar (DWM attributes), own taskbar identity + icon
  - rotating log file `logs\deepfind.log`; GUI processes have no console, stdout/stderr are redirected
  - `--headless` (engine only, prints the URL — used for smoke tests) and `--debug` (WebView2 devtools)
- **`packaging/build_windows.sh`** — builds `dist/DeepFind-Setup-<ver>.exe` + a portable zip from
  Linux/WSL/CI. No Windows machine, PyInstaller or Rust needed:
  relocatable CPython 3.11 (python-build-standalone) → pip cross-installs `win_amd64` wheels →
  React build + sources copied into `app\` → byte-compiled → native launcher → NSIS installer.
  `WITH_MODELS=1` bundles `./models` into the installer.
- **`packaging/windows/launcher.c`** — `DeepFind.exe`, a 60 KB MinGW launcher that loads
  `runtime\python311.dll` **in-process** (so the process really is DeepFind.exe: correct icon in the
  taskbar/Task Manager, no console, no stray `pythonw.exe`), with version info + DPI/long-path manifest.
  Points the std handles at NUL first (GUI processes have none). `DeepFind-console.exe` is the same
  launcher with a console window and live logs, for troubleshooting.
- The installer is a 64-bit NSIS build (the app is x64-only).
- **`packaging/windows/deepfind.nsi`** — per-user installer (no admin prompt) into
  `%LOCALAPPDATA%\Programs\DeepFind`; Start-menu + desktop shortcuts; Apps & Features entry;
  checks for the WebView2 runtime; refuses to overwrite while the app is running; uninstaller asks
  before deleting the index/models.
- **`packaging/windows/requirements-windows.txt`** — runtime deps for the Windows build (adds the
  Windows-only packages pip skips when cross-installing: pythonnet, colorama, win32-setctime…).
- **`.github/workflows/windows-build.yml`** — CI: tests → installer → uploaded artifact
  (optional input to bundle the models).
- **`packaging/make_icon.py`** → `desktop/deepfind.ico` (regenerate when the logo changes).

### Added — frontend (`frontend/`, React 19 + Vite + TypeScript)
- Views: **Search** (idle "poster" page with suggestions/recent searches, results list with type
  filters, keyboard navigation, preview pane with image/PDF thumbnails, open / show in folder /
  copy path), **Folders** (add via native picker, remove with confirmation, per-folder file counts),
  **Activity** (engine state, queues, CPU/RAM vs. limits + 2-minute CPU sparkline, library
  breakdown by kind), **Settings** (CPU/RAM limits, pause on battery, AI-model download, texture,
  recent-search clearing, data/log folders).
- Design "Cobalt" — two-colour print look taken from the reference poster: ink-blue ground,
  off-white ink, woven paper texture, dry-brush stamped headings (SVG filters, no images), vertical
  editorial rail (section no. / title / date), thin rules and "credit lines" instead of cards,
  **menu bar at the bottom**. No gradients/glows/glass. Text contrast ≥ 4.7:1 everywhere.
  Fonts bundled offline (Archivo, IBM Plex Sans/Mono/Serif).
- Keyboard: `Ctrl+K` or `/` focus search · `↑↓` move · `Enter` open · `Shift+Enter` show in folder ·
  `Ctrl+C` copy path · `Esc` clear · `Alt+1..4` switch section.
- Relevance bars are relative to the best hit (a perfect keyword-only hit has a raw fused score of
  ~0.5 and would otherwise read as "50 %"); the raw score is still shown in the preview.
- Design history: v1 "Violet Mochi" (anime-poster theme) → v2 "Nightfall" (purple starfield, top
  menu) → v3 "Cobalt" (current), per review feedback that v2 looked machine-generated.

### Added — backend
- `POST /api/open` — open a file with its default app (only files that are in the index).
- `GET /api/thumb?path=&size=` — JPEG preview of an indexed image or PDF page 1 (LRU-cached).
- `GET /api/library` — `{total, types: {ext: n}, folders: {folder: n}}`.
- `GET /api/models`, `POST /api/models/download` (`backend/models.py`) — model status and one-click
  download. Runs `scripts/download_models.py` in a **subprocess** without `HF_HUB_OFFLINE`, so the
  engine itself never touches the network; when done, the encoder and vision are re-enabled and
  folders are rescanned (images indexed by name only get OCR/CLIP) — no restart.
- `GET /` — the backend serves the built UI (`frontend/dist` or `DEEPFIND_UI_DIR`) from the same
  origin: no CORS, no port handshake.
- `/api/health` returns `version`; `/api/status` adds `semantic_available` / `semantic_error`.
- `--parent-pid` flag: the API exits if the process that launched it dies.
- Model paths: `DEEPFIND_MODELS_DIR` sets both model folders; a frozen build looks for `models\`
  next to the executable.

### Changed / fixed
- **search_engine**: a missing/broken CLIP text model no longer makes every search fail with 500 —
  search falls back to **keyword-only** (score normalised for one list) and retries loading at most
  once a minute. New: `type_counts()`, `count_under()`, `semantic_status()`, `reset_encoder_error()`.
- **pipeline**: `models_changed()` re-enables vision and rescans after models are installed.
- **download_models.py**: downloads to `*.part` then renames (a failed download can't leave a
  file that looks installed); unbuffered progress output.
- `requirements.txt`: `pyinstaller` → `pywebview` (the Tauri + PyInstaller-sidecar plan was replaced —
  see "Why not Tauri" below).
- `.gitignore`: Tauri entries replaced by build output. CORS: only the Vite dev origin remains
  (the desktop UI is same-origin).

### Tests
- 16 → 19 tests: desktop endpoints (open/thumb/library + path guards), keyword-only fallback,
  model status + UI mount. `pytest tests` still needs no models.

### Why not Tauri (as planned in the README)
Tauri needs the Rust Windows toolchain and a PyInstaller sidecar built *on Windows*. The
pywebview shell uses the same WebView2 engine Tauri uses on Windows, keeps the whole app in one
language, and the installer can be produced from any Linux/WSL machine or CI.

### Known limits
- The installer does **not** include the AI models unless built with `WITH_MODELS=1`; out of the box
  search is keyword-only until *Settings → Download models* (~350 MB, once, needs internet).
- Needs the Microsoft Edge WebView2 runtime (preinstalled on Windows 11 and current Windows 10;
  the installer links to it if missing).
- The installer is unsigned: Windows SmartScreen shows "unknown publisher" → *More info → Run anyway*.
- Not yet run on a physical Windows PC — the WebView2 window itself can't be exercised off-Windows.

### Verified in this session
- `pytest`: 19/19 pass. `tsc --noEmit` clean; `vite build` clean.
- Every screen screenshotted (1280×800 and 900×620) against the real backend with demo files.
- The **built Windows app under Wine 9**: `runtime\python.exe` imports every dependency
  (lancedb, pymupdf, onnxruntime, fastembed, rapidocr, pywebview, pythonnet…);
  `DeepFind-console.exe --headless` starts the engine, indexes a folder by its Windows path
  (PDF/DOCX/XLSX/PPTX/MD), answers searches, renders PDF thumbnails, serves the UI, and the
  model downloader subprocess starts (it then fails only because this sandbox has no internet).

---

## 0.1.x — engine integration (previous round)
NEW: pipeline.py (Tier 2 glue), backend/main.py (FastAPI, was empty), tests/ (16 tests), demo_integration.py
FIXED
- search_engine: text encoder now uses ./models/clip_cache (offline!); thread-safe; recursive delete; exists/count;
  normalised 0-1 score; highlighted snippets; "[Visual match]" for images; bad/NaN vectors -> zeros; periodic optimize()
- ingestion: hash committed only AFTER indexing (was: failed/queued files never retried); worker queue instead of
  blocking the watchdog thread; no 0.5 s sleep per old file; ignores .venv/node_modules/.git/etc.; mixed PDFs (some scanned
  pages) now detected; folder delete/move handled; 5 MB text cap; get_stats(); stale Tier-2 items dropped on restart
- vision: per-page PDF OCR; OCR and CLIP fail independently; clear error when models are missing
- search_engine: removed deprecated LanceDB calls (table_names, create_fts_index) — old versions still supported
- tests: old scripts (busy-wait `while True: pass`, ran at import) replaced by pytest
REMOVED from the zip: .venv, test_lancedb, tmp_lancedb_check, caches (304 MB -> ~60 KB)
