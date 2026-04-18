# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
python main.py

# Python tests (209 passing)
python -m pytest tests/ -q
python -m pytest tests/test_racebox_data.py -q          # single file
python -m pytest tests/ -k "test_delta" -q              # single test by name

# JS tests (frontend/tests/, run with Vitest + jsdom; 39 passing)
npm run test:run           # one-shot
npm test                   # watch mode

# Build Windows .exe (onedir, outputs to dist/OpenLap/)
pip install pyinstaller
pyinstaller OpenLap.spec --clean -y
# ffmpeg.exe / ffprobe.exe must be on PATH or placed next to OpenLap.spec
```

## Architecture

OpenLap is a **PyWebView desktop app**: Python is the backend, a vanilla-JS/HTML Canvas frontend is the UI. There is no web server — pywebview loads `frontend/index.html` directly as a local file.

### Frontend ↔ Backend communication

Two channels:

1. **JS → Python (RPC):** `await window.pywebview.api.method_name(args)` — every public method on `WebviewAPI` (in `webview_api.py`) is callable from JS. Return values must be JSON-serialisable.
2. **Python → JS (push events):** `WebviewAPI._push(event_type, **payload)` calls `window.evaluate_js(...)` to fire a `CustomEvent('openlap', {detail})` that JS listens for with `window.addEventListener('openlap', ...)`.

Video playback uses a third channel: a local HTTP server (`_VideoFileHandler` in `webview_api.py`) on a random port that serves arbitrary local files with HTTP range support. JS gets the port via `get_video_server_port()` and builds URLs like `http://127.0.0.1:{port}/?f={encodedPath}`.

### Dual rendering stacks

Every gauge style exists **twice**:

| Stack | Location | Used for |
|---|---|---|
| JS Canvas renderers | `frontend/js/gauges/*.js` | Live preview in overlay editor |
| Python/matplotlib plugins | `styles/*.py` | Video export frames |

When adding or changing a gauge style, **both** must be updated to stay in sync. `base.js` and `overlay_utils.py` / `overlay_themes.py` define the shared drawing primitives and theme tokens — keep them consistent.

### Python style plugins

Each `.py` in `styles/` must export:
- `STYLE_NAME: str` — display name
- `ELEMENT_TYPE: str` — `"gauge"` or `"map"`
- `render(data, w, h) -> np.ndarray` — returns RGBA array shape `(h, w, 4)`

`style_registry.py` auto-discovers plugins at runtime. `render()` receives a `data` dict with `_tc` (theme colour tokens, injected by `style_registry.render_style`) and `_theme` (theme name string).

### Data model

All four loaders (racebox, aim, gpx, motec) return the same types from `racebox_data.py`: `Session`, `Lap`, `DataPoint`. Never add source-specific fields to `DataPoint`. `session_scanner.py` drives scanning across all configured folders and maintains `~/.openlap/scan_cache.json`.

### Config

`AppConfig` dataclass persisted to `~/.openlap/config.json`. Overlay layout is nested as `OverlayLayout` (with `gauges: List[dict]`). Named presets live in `AppConfig.presets` (name → serialized `OverlayLayout` dict). On load, if `active_preset` is set the overlay is always rebuilt from the preset — unsaved edits to the live layout are discarded on restart.

Sync offsets are stored in three fields: `offsets` (csv_path → float), `offset_sources` (csv_path → `'user'`|`'auto'`), and `auto_sync_failed` (list of csv_paths where auto-sync was tried but confidence was too low).

### Auto-sync pipeline

`auto_sync.py` detects the video-telemetry sync offset automatically using cross-correlation of video motion signal vs telemetry G-force magnitude. Runs as a background thread in `WebviewAPI._run_auto_sync_bg()` after each scan (opt-in via `auto_sync_enabled`). Key parameters: 5 fps decode, 320px wide frames, ±120s search window, confidence threshold 6× (need ≥3× to write). Uses `CREATE_NO_WINDOW` on Windows so no terminal flashes appear. Export cancels any running auto-sync via `_auto_sync_cancel` event. Auto results (`source='auto'`) are never written over a user-confirmed offset (`source='user'`).

### Video export pipeline

`export_runner.py` → `video_renderer.render_lap()` → multiprocessing pool of `overlay_worker.py` workers (one worker per frame). Workers call `style_registry.render_style()`. Requires `freeze_support()` on Windows (called in `main.py`).

### Frontend structure

`frontend/js/`:
- `api.js` — thin wrappers around `window.pywebview.api` calls
- `state.js` — client-side app state
- `router.js` — SPA page switching
- `pages/` — per-tab logic (Data, Overlay, Export, Settings)
- `gauges/` — Canvas gauge renderers; `base.js` has shared utilities (`drawBackground`, `scaleFont`, `fmtValue`, theme definitions)

