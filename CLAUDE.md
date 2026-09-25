# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
python main.py

# Python tests (765 passing)
python -m pytest tests/ -q
python -m pytest tests/test_racebox_data.py -q          # single file
python -m pytest tests/ -k "test_delta" -q              # single test by name

# JS tests (frontend/tests/, run with Vitest + jsdom; 115 passing)
npm run test:run           # one-shot
npm test                   # watch mode

# Build Windows .exe (onedir, outputs to dist/OpenLap/)
pip install pyinstaller
pyinstaller OpenLap.spec --clean -y
# ffmpeg.exe / ffprobe.exe must be on PATH or placed next to OpenLap.spec

# Verify a build can import every module, style plugin and dependency
# (PyInstaller silently omits anything not installed; CI runs this after building)
python scripts/smoke_test_build.py --extra racebox-download -- dist/OpenLap/OpenLap.exe
python scripts/smoke_test_build.py -- python main.py      # same check from source
```

CI: `.github/workflows/test.yml` runs pytest (Windows + Linux) and Vitest on every push to main and every PR. `release.yml` calls it first, so a failing suite blocks a release. Release dependencies are installed from `pyproject.toml` — add new runtime packages there, never to the workflow.

## Architecture

OpenLap is a **PyWebView desktop app**: Python is the backend, a vanilla-JS/HTML Canvas frontend is the UI. There is no web server — pywebview loads `frontend/index.html` directly as a local file.

### Frontend ↔ Backend communication

Two channels:

1. **JS → Python (RPC):** `await window.pywebview.api.method_name(args)` — every public method on `WebviewAPI` (in `webview_api.py`) is callable from JS. Return values must be JSON-serialisable.
2. **Python → JS (push events):** `WebviewAPI._push(event_type, **payload)` calls `window.evaluate_js(...)` to fire a `CustomEvent('openlap', {detail})` that JS listens for with `window.addEventListener('openlap', ...)`.

Video playback uses a third channel: a local HTTP server (`_VideoFileHandler` in `webview_api.py`) on a random port that serves local video and image files with HTTP range support — only extensions in `session_scanner.VIDEO_EXTENSIONS`/`IMAGE_EXTENSIONS`, and only paths the app registered (scanned, assigned, picked in a dialog, or used by a layout). JS gets the port via `get_video_server_port()` and builds URLs like `http://127.0.0.1:{port}/?f={encodedPath}`.

### Dual rendering stacks

Every gauge style exists **twice**:

| Stack | Location | Used for |
|---|---|---|
| JS Canvas renderers | `frontend/js/gauges/*.js` | Live preview in overlay editor |
| Python/matplotlib plugins | `styles/*.py` | Video export frames |

When adding or changing a gauge style, **both** must be updated to stay in sync. `base.js` and `overlay_utils.py` / `overlay_themes.py` define the shared drawing primitives and theme tokens — keep them consistent. `tests/test_preview_export_parity.py` fails if a style plugin lacks an editor entry or JS renderer, or if the editor's channel table or history window (`HISTORY_WINDOW_S`, 4 s resampled to `HISTORY_POINTS`) drifts from `gauge_channels.py`. The `Video` gauge type has no style plugin: FFmpeg overlays a second recording into its box (`video_layers.py`).

### Python style plugins

Each `.py` in `styles/` must export:
- `STYLE_NAME: str` — display name
- `ELEMENT_TYPE: str` — `"gauge"` or `"map"`
- `render(data, w, h) -> np.ndarray` — returns RGBA array shape `(h, w, 4)`

`style_registry.py` auto-discovers plugins at runtime. `render()` receives a `data` dict with `_tc` (theme colour tokens, injected by `style_registry.render_style`) and `_theme` (theme name string).

### Data model and loading

All loaders (racebox, aim, gpx, motec, vbox, unipro, gopro) return the same types from `data_model.py`: `Session`, `Lap`, `DataPoint`. Never add source-specific fields to `DataPoint`. **`session_loader.py` is the only place a file becomes a Session**: `load_file` (format detected from content), `load_merged` (+ secondary telemetry, + the user's track lines), and `SessionCache` for the UI's read-only endpoints (callers must not mutate cached sessions; export loads uncached because it does).

Laps are always cut by `data_model.build_laps`: contiguous runs of the lap column, each lap timed to the next lap's start, boundaries refined to the start/finish line crossing between samples (`lap_detection.py`), one classification rule set. Sources without laps (GPX, `.uni`, GoPro) get laps from `laps_from_track` (automatic line). User start/finish and sector lines (`AppConfig.track_lines`) are matched to sessions by place, not name.

`session_scanner.py` groups clips into recordings per camera (folder + format) and only by frame-contiguous chapters or DJI/GoPro recording numbers, matches sessions by start proximity, and maintains `~/.openlap/scan_cache.json`. When a rescan changes a session's first clip, `WebviewAPI._migrate_offsets` keeps its sync offset valid (shift / clear auto / flag user for review).

### Config

`AppConfig` dataclass persisted to `~/.openlap/config.json`. Overlay layout is nested as `OverlayLayout` (with `gauges: List[dict]`). Named presets live in `AppConfig.presets` (name → serialized `OverlayLayout` dict). On load, if `active_preset` is set the overlay is always rebuilt from the preset — unsaved edits to the live layout are discarded on restart.

Sync offsets are stored in `offsets` (csv_path → float), `offset_sources` (csv_path → `'user'`|`'auto'`), `auto_sync_failed` (tried, confidence too low) and `offset_review` (user offsets whose video changed on rescan). GoPro sessions are on the camera's own clock: offset 0, source `'camera'`.

### Auto-sync pipeline

`auto_sync.py` detects the video-telemetry sync offset automatically using cross-correlation of video motion signal vs telemetry G-force magnitude; a match must also beat the best rival peak by `MIN_PEAK_MARGIN` (laps repeat, so ambiguity is the normal failure). The same correlator syncs a second telemetry file (`correlate_channels`) and a second camera by audio (`audio_offset`). Runs as a background thread in `WebviewAPI._run_auto_sync_bg()` after each scan (opt-in via `auto_sync_enabled`). Key parameters: 5 fps decode, 320px wide frames, ±120s search window, confidence threshold 6× (need ≥3× to write). Uses `CREATE_NO_WINDOW` on Windows so no terminal flashes appear. Export cancels any running auto-sync via `_auto_sync_cancel` event. Auto results (`source='auto'`) are never written over a user-confirmed offset (`source='user'`).

### Video export pipeline

`export_runner.py` → `video_renderer.render_lap()`. One FFmpeg process per output reads the source clips in place (the clips the window covers, keyframe-seeked; never through the concat demuxer, which cannot seek), and a worker pool (shared across an export's laps) draws each frame's gauges with `style_registry.render_style()` into a compact atlas (`overlay_worker.atlas_layout`) piped to FFmpeg, which crops the tiles back out and overlays them; video and audio are encoded in one pass. Overlay-only exports overlay the same tiles on a transparent frame (ProRes 4444). Output goes to `<name>.part.<ext>` and is renamed only on success; every skipped or failed item is counted. Frame window = the Lap 1 start contract (`frame_window`, see `tests/test_lap1_start_contract.py`). Requires `freeze_support()` on Windows (called in `main.py`).

### Build verification

`python main.py --self-test <report.json> <modules…>` (see `self_test.py`) imports modules inside a packaged build; `scripts/smoke_test_build.py` drives it with every module, style and `pyproject.toml` dependency. CI (`.github/workflows/test.yml`) runs tests on Windows and Linux; `release.yml` installs from `pyproject.toml`, runs the tests, builds, then smoke-tests the exe.

### Frontend structure

`frontend/js/`:
- `api.js` — thin wrappers around `window.pywebview.api` calls
- `state.js` — client-side app state
- `router.js` — SPA page switching
- `pages/` — per-tab logic (Data, Overlay, Export, Settings)
- `gauges/` — Canvas gauge renderers; `base.js` has shared utilities (`drawBackground`, `scaleFont`, `fmtValue`, theme definitions)

