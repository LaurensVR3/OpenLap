# OpenLap — Todo

Findings from the full code/feature review (2026-09-25). Roughly in suggested fix order.

## Critical

- [x] **Release builds ship without scipy** — `auto_sync.py` imports `scipy.signal`, but `.github/workflows/release.yml` never installs it (v0.3.3 zip contains no scipy). Video auto-sync and secondary-telemetry sync die with an ImportError in a background thread; UI never gets `auto_sync_done` / `channel_sync_done`.
- [x] **Release builds ship without requests** (found while doing the next item) — RaceBox cloud download imports `requests`, which the release workflow never installed; absent from the v0.3.3 exe. Fixed by installing the release from `pyproject.toml`.
- [x] **CI never runs tests** — only workflow is the tag-triggered release build. Add a PR/push job running pytest + vitest, and a post-build smoke test that imports every module inside the frozen build.
- [x] **Export ignores secondary telemetry** — `export_runner.load_any_session` never calls `merge_sessions`, so gauges bound to secondary-file channels render blank/zero in exported video while the preview shows them. Route export through the same loader as `WebviewAPI._load_session`.
- [x] **Mux failure reported as success** — `video_renderer.py` (end of `render_lap`) logs the failure, leaves `_raw.avi`, and returns normally.
- [x] **Skipped export items reported as success** — "no video", "no timed lap", "CSV not found" in `export_runner.py` aren't added to `errors`, so the run can report "Done — N exported" with no output.

## Correctness bugs

- [ ] **Lap durations short by one sample interval** — all loaders use `pts[-1] - pts[0]` (`racebox_data.py`, `aim_data.py`, `vbox_data.py`, `unipro_data.py`, `motec_data.py`); 60.000s laps at 25 Hz show as 59.960s. Use next lap's start (or device-reported lap times).
- [x] **Export filenames collide / overwrite silently** — `selected_lap` labels by index incl. outlap (`Lap{lap_idx+1}`), `all_laps` by timed-lap ordinal; "Lap03" means different laps. Use `lap_num` consistently and never overwrite without a suffix.
- [x] **Joined-video cache can serve the wrong video** — keyed by CSV basename + mtime only (`export_runner.py`, `joined_{basename}.mp4`). Key by hash of the clip list; add cleanup of `~/.openlap/video_cache`.
- [ ] **Channel (telemetry-vs-telemetry) sync lacks peak-margin check** — `auto_sync.correlate_channels` uses confidence only; lap-periodic Speed traces will produce confidently wrong offsets. Apply `MIN_PEAK_MARGIN` like video sync.
- [ ] **Video auto-sync search window fixed at ±120s around 0** — centre it on the clock-derived prior (video creation_time − csv_start) so cameras started >2 min before the logger still sync.
- [ ] **Frame stepping assumes 30 fps** — `frontend/js/pages/data.js` (`let fps = 30`). Get real fps from ffprobe (store in scan cache) or `requestVideoFrameCallback`.
- [ ] **XSS via telemetry channel names** — `editor.js` Multi-Line picker (`<option value="${o.value}">${o.label}</option>`) and `_channelLabel` output are unescaped; channel names come from telemetry files and page script can call `pywebview.api`. Also escape preset names in `rebuildPresetSelector`.
- [ ] **Video extension allowlists incomplete** — `session_scanner.VIDEO_EXTENSIONS` and `webview_api._ALLOWED_VIDEO_EXTENSIONS` miss `.mts`, `.m2ts`, `.webm`, mixed case (`.Mp4`). Compare case-insensitively; keep one shared list.

## Preview ≠ export parity

- [x] **Info gauge time is UTC in export, local in preview** — `video_renderer._build_session_meta` formats `date_utc` directly.
- [x] **Multi-Line drops dynamic channels in export** — `gauge_channels.build_multi_data` skips anything not in `GAUGE_CHANNELS`.
- [x] **Dynamic channels lose label/unit in export** — `overlay_worker.py` calls `gauge_data(channel, history, ...)` without `extra_label`/`extra_unit`; pass `session.extra_channel_meta` through.
- [ ] **Line gauge history window differs** — preview: last 41 telemetry samples (`editor.js` `histStart`); export: last 120 video frames (`styles/gauge_line.py`). Make both time-based (e.g. N seconds).
- [ ] **Reference-lap gauges can't be previewed** — Delta, Compare, Splits, Sector Bar and the ref ghost on maps use dummy/empty data in the editor. Compute delta/reference data for the preview too.
- [ ] **Dynamic-channel auto-range rescales every frame** — makes Bar/Dial meaningless; use session-wide min/max.
- [ ] **Preview ignores session_info track override** — editor uses raw `meta.track`; export uses the renamed track.

## Performance / architecture

- [x] **No session cache** — every RPC re-parses the file (`WebviewAPI._load_session`): 4 full parses to open a session in the editor, another per lap switch, ~1s extra per secondary merge; `get_laps_for_ref_picker` parses the whole library. Add an LRU keyed by (path, size, mtime, secondary, offset).
- [x] **Move compositing into FFmpeg** — current path: cv2 decode → MJPG intermediate → re-encode (double lossy), full frame + ~600 history dicts pickled per frame. Render only the RGBA overlay (existing overlay-only path) and composite with FFmpeg `overlay`; enables hwaccel decode and exact audio/frame timing.
- [x] **Per-frame matplotlib cost** — ~55 ms/frame/core for the default layout. Cache static gauges (Info, Image), skip re-render when the displayed value is unchanged, reuse figures or move to PIL/Skia.
- [x] **Reuse the worker Pool across laps** in an all-laps export instead of spawning per `render_lap`.
- [ ] **Video matching by start-time proximity** (`session_scanner.match_sessions`, 1h window, no one-to-one). Match by time-range overlap instead.
- [ ] **Multi-camera clips merged** — `group_videos` groups purely by time; front/rear cameras get concatenated. Group per camera (model tag / resolution / folder).
- [ ] **Timezone risk (unverified)** — MoTeC header time treated as UTC; many action cams write local time tagged as UTC.

## Code health

- [x] Unify loader dispatch — three divergent copies (`webview_api._load_one_session`, `export_runner.load_any_session`, `auto_sync._load_session`).
- [ ] Remove dead code: `BatchState` / `SessionState` / `build_batch_state` and `CSV_SOURCE_*` (`session_scanner.py`), `save_scan_cache` (`app_config.py`).
- [ ] `styles/map_progress.py` is orphaned (no JS twin, not in editor, not routed as a map in `overlay_worker`) — wire it up or delete.
- [ ] README claims dropping a `.py` into `styles/` makes it appear in the UI — false (editor type list is hard-coded; styles are bundled in `_internal`). Fix the claim or the mechanism.
- [ ] Update stale test counts in `CLAUDE.md` (now 638 py / 110 JS).
- [ ] Minor: `install_playwright_chromium` uses raw `subprocess.Popen` (console flash on Windows); cache files (`scan_cache.json`, `file_meta_cache.json`) aren't written atomically; in/out-lap heuristics differ between loaders.

## Feature gaps

- [ ] Embedded camera telemetry: GoPro GPMF, DJI, Insta360.
- [ ] GPS lap detection with user-defined start/finish line and sectors (GPX is one lap; `.uni` is heuristic; sectors are fixed thirds).
- [ ] Multi-camera / picture-in-picture.
- [ ] Side-by-side / ghost comparison of two laps' videos.
- [ ] Editor: undo/redo, keyboard nudge/delete/duplicate, delete/rename presets.
- [ ] Export: output resolution/fps, bitrate control; persist export queue across restarts.
- [ ] Update-available notification.
