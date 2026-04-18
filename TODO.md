# OpenLap — Todo

## v0.1.3 — Auto Sync
- [x] Cross-correlation auto-sync engine (`auto_sync.py`) — streams ffmpeg frames at 5 fps, checks confidence every 20s, stops early when threshold is reached
- [x] Sync status system: `no_video` / `unset` / `~ auto` (blue) / `✓ user` (green)
- [x] Auto-sync runs as opt-in background thread after every scan; progress shown in status bar
- [x] Export cancels any running auto-sync; auto results never overwrite user-confirmed offsets
- [x] Settings toggle: **Enable auto-sync on scan** (off by default)
- [x] Align Video card shows confirmation banner for auto-detected offsets; Mark promotes to user

## Build & distribution
- [x] Add `frontend/icon.ico` and uncomment the icon line in `OpenLap.spec`

## Code review — Security (fix before next release)
- [x] **Path traversal in video server** (`webview_api.py`) — only serve recognised video extensions; 403 on anything else
- [x] **Unsafe Range header parsing** (`webview_api.py`) — try/except around int() parse, 400 on bad input, 416 on out-of-range
- [x] **Unsafe seek bounds** (`webview_api.py`) — 416 if `start >= file_size`

## Code review — Concurrency / stability (fix before next release)
- [x] **Race condition in export/RaceBox threads** (`webview_api.py`) — `_thread_lock` protects `_export_thread` / `_rb_thread` assignment
- [x] **Unvalidated worker/CRF inputs** (`webview_api.py`) — clamped in `_run_export_bg` before passing to `run_export`
- [x] **Silent overlay render failures** (`overlay_worker.py`) — all `except Exception: pass` replaced with `logger.debug`
- [x] **Swallowed OSError in scanner** (`session_scanner.py`) — logs warning instead of silently passing
- [x] **Truncated FFmpeg errors** (`video_renderer.py`) — full stderr logged at ERROR level; tail surfaced in exception

## Code review — Input validation & robustness
- [x] **Negative lap index accepted** (`export_runner.py`) — guards `lap_idx < 0` as well as `>= len`
- [x] **Unvalidated image loading** (`overlay_worker.py`) — 50 MB size cap before PIL.open
- [x] **NaN propagation in interpolation** (`video_renderer.py`) — `math.isfinite()` guard on `np.interp` result
- [x] **JSON serialization fragility** (`app_config.py`) — `default=str` in `json.dump` as safety net

## Code review — Testing gaps
- [x] Tests for Range request parsing in `_VideoFileHandler` (`tests/test_video_server.py`)
- [x] Tests for export thread safety / clamping (`tests/test_webview_api.py`)
- [x] Tests for video group matching in `session_scanner.py` (covered by existing `test_session_scanner.py`)
- [x] Tests for sync offset frame range and render helper functions (`tests/test_video_renderer.py`)

## Code review — Architecture & code quality
- [x] **Circular dependency in loaders** — move `DataPoint`, `Lap`, `Session` to `data_model.py` so loaders don't import from `racebox_data.py`
- [x] **Lean angle sign normalization** (`gauge_channels.py` ~L79) — move negation to each data loader so `DataPoint.lean_angle` has a consistent convention everywhere
- [x] **Hardcoded sector count** (`video_renderer.py`) — `_N_SECTORS = 3` module-level constant
- [x] **Inlap heuristic undocumented** (`racebox_data.py`) — `_INLAP_SLOWNESS_THRESHOLD = 1.5` named constant with comment
- [x] **No `FFMPEG_BIN` env override** (`webview_api.py`) — checks `os.environ.get('FFMPEG_BIN')` first
- [x] **Missing type hints** (`session_scanner.py`, `overlay_worker.py`) — `progress_cb: Optional[Callable[...]]` and `args: Tuple` annotated
