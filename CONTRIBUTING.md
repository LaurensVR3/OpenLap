# Contributing to OpenLap

Thanks for your interest. OpenLap is a PyWebView desktop app — Python backend, vanilla-JS/HTML Canvas frontend. Read `CLAUDE.md` first for the full architecture overview.

## Getting started

```bash
git clone https://github.com/LaurensVR3/OpenLap.git
cd OpenLap
pip install -e ".[dev]"
python main.py
```

FFmpeg must be on your PATH. On Windows: `winget install Gyan.FFmpeg`.

## Running tests

```bash
# Python
python -m pytest tests/ -q

# JavaScript (requires Node)
npm install
npm run test:run

# Every module, style plugin and dependency imports (also run by CI on the packaged build)
python scripts/smoke_test_build.py -- python main.py
```

CI (`.github/workflows/test.yml`) runs all of these on Windows and Linux for every push and PR. All tests must pass before opening a PR. If you're adding a feature, add a test.

## Project layout

```
main.py               entry point, freeze_support for multiprocessing
webview_api.py        every public method here is callable from JS
auto_sync.py          background video-telemetry sync detection (cross-correlation)
app_config.py         AppConfig dataclass — persisted to ~/.openlap/config.json
frontend/             vanilla JS + HTML, no build step
  js/pages/           one file per tab (data, editor, export, settings)
  js/gauges/          JS canvas renderers — one per gauge style
styles/               Python/matplotlib renderers — one per gauge style
tests/                pytest (Python) + Vitest (JS in frontend/tests/)
```

The two rendering stacks (`styles/*.py` and `frontend/js/gauges/*.js`) must stay in sync. When you change a gauge style, update both.

## Adding a gauge style

1. Copy `styles/gauge_numeric.py` → `styles/gauge_myname.py`. Set `STYLE_NAME` and implement `render(data, w, h) -> np.ndarray` (RGBA). It is discovered by `style_registry.py` and drawn in exports.
2. Add it to `GAUGE_TYPES` in `gauge_channels.py` with its bucket (`single`, `multi` or `none` — see the comment there).
3. Copy `frontend/js/gauges/numeric.js` → `frontend/js/gauges/myname.js`, implement `GaugeMyname.render(ctx, data, w, h)`, and load it from `frontend/index.html`.
4. In `frontend/js/pages/editor.js`, add it to `GAUGE_RENDERERS` and `GAUGE_TYPES` (same name and bucket).
5. Run the tests: `tests/test_preview_export_parity.py` fails if a style is missing from any of these places.
6. Add `styles.gauge_myname` to `hidden_imports` in `OpenLap.spec`; the build's smoke test checks it was bundled.

## Adding a telemetry channel

Channels are defined in `data_model.py` (`DataPoint` dataclass). All four loaders must populate the same fields — never add source-specific fields to `DataPoint`.

## Good first issues

- **Add a test** for an untested module (`webview_api.py`, `style_registry.py`, any gauge style)
- **Improve an error message** — search for `console.warn` or `logger.exception` and see if the user-facing message could be clearer
- **Add a gauge style** — see above

## Pull requests

- Keep PRs focused — one feature or fix per PR
- Match the existing code style (no linter enforced, just be consistent)
- Update `README.md` if you add user-visible functionality
- If your change touches the overlay editor or export pipeline, test an actual export on a real session before opening the PR
