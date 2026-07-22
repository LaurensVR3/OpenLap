/**
 * Test helpers for loading IIFE-style frontend modules into the jsdom global scope.
 *
 * All page modules are browser IIFEs that reference State, API, Router as free
 * variables and self-register via Router.register().  We simulate that by:
 *   1. Setting the mocks on globalThis before loading.
 *   2. Executing the file body via new Function so free variable lookups hit globalThis.
 *   3. Extracting the registered page from the Router mock.
 */
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const JS_ROOT   = resolve(__dirname, '../js');

// ── Module loaders ────────────────────────────────────────────────────────────

/**
 * Load state.js and assign a fresh State instance to globalThis.State.
 * Call this in beforeEach so each test gets isolated reactive state.
 */
export function loadState() {
  const code = readFileSync(resolve(JS_ROOT, 'state.js'), 'utf8');
  // state.js uses `const State = (() => {...})()`.  Running it inside a
  // Function body makes `State` a local; we return it and pin to globalThis.
  globalThis.State = new Function(`${code}; return State;`)();
}

/**
 * Load a page IIFE (e.g. 'pages/export.js').
 * Expects globalThis.State / API / Router to already be set.
 * The IIFE self-registers with the Router mock via Router.register().
 */
export function loadPage(relPath) {
  const code = readFileSync(resolve(JS_ROOT, relPath), 'utf8');
  new Function(code)();
}

/**
 * Load gauges/base.js and return the GaugeBase namespace object.
 * base.js defines top-level `const GaugeBase = {...}` with no IIFE wrapper,
 * so we return it directly from the Function body (same trick as loadState).
 */
export function loadGaugeBase() {
  const code = readFileSync(resolve(JS_ROOT, 'gauges/base.js'), 'utf8');
  return new Function(`${code}; return GaugeBase;`)();
}

/**
 * Load any other gauges/*.js file (e.g. 'bar.js' → GaugeBar) and return its
 * top-level export. These files reference `GaugeBase` as a free global (the
 * same way page IIFEs reference State/API/Router), so pin loadGaugeBase()'s
 * result to globalThis.GaugeBase before calling this.
 */
export function loadGauge(fileName, exportName) {
  const code = readFileSync(resolve(JS_ROOT, `gauges/${fileName}`), 'utf8');
  return new Function(`${code}; return ${exportName};`)();
}

/**
 * Load the real router.js and return the Router namespace object.
 * Like gauges/base.js, it defines a top-level `const Router = (() => {...})()`
 * with no IIFE-registers-itself wrapper, so we return it directly (same
 * trick as loadGaugeBase). Page tests use the makeRouter() *mock* instead —
 * this loader is for testing router.js's own behaviour.
 */
export function loadRouter() {
  const code = readFileSync(resolve(JS_ROOT, 'router.js'), 'utf8');
  return new Function(`${code}; return Router;`)();
}

/**
 * Load export_params.js and pin ExportParams to globalThis.
 * Like gauges/base.js, it defines a top-level `const ExportParams = {...}`
 * with no IIFE wrapper. Page modules (editor.js) reference `ExportParams` as
 * a free/global variable, the same way they reference State/API/Router — so
 * unlike loadGaugeBase() (whose return value callers use directly), this one
 * must be pinned to globalThis for page IIFEs loaded afterwards to see it.
 */
export function loadExportParams() {
  const code = readFileSync(resolve(JS_ROOT, 'export_params.js'), 'utf8');
  globalThis.ExportParams = new Function(`${code}; return ExportParams;`)();
}

/**
 * Minimal CanvasRenderingContext2D stand-in for testing font-fit logic.
 * jsdom does not implement real text metrics, so measureText() here
 * approximates width from the font-size number embedded in ctx.font
 * (set as "<weight> <size>px <family>") and the string length — good enough
 * to unit-test shrink/clamp behaviour, not for pixel-perfect assertions.
 */
export function makeFakeCanvasCtx() {
  return {
    font: '',
    measureText(text) {
      const m = /(\d+(?:\.\d+)?)px/.exec(this.font);
      const size = m ? parseFloat(m[1]) : 10;
      return { width: text.length * size * 0.55 };
    },
  };
}

/**
 * A permissive CanvasRenderingContext2D stand-in that supports a *full*
 * gauge render(ctx, data, w, h) call — every drawing method the 15 gauge
 * files use (fillRect, beginPath, moveTo/lineTo, arc, stroke, fill, save/
 * restore, clip, translate, rotate, createLinearGradient, …) is a harmless
 * no-op via a Proxy, and any property (fillStyle, textAlign, lineWidth, …)
 * is freely settable. Only `fillText` is a real spy — recorded in the
 * returned `._fillTextCalls` array as {text, x, y} — since that's what
 * gauge-formatting regression tests need to assert against; nothing else
 * about the render's actual pixel output is verified this way.
 */
export function makeFullCanvasCtx() {
  const fillTextCalls = [];
  const base = {
    font: '',
    measureText(text) {
      const m = /(\d+(?:\.\d+)?)px/.exec(this.font);
      const size = m ? parseFloat(m[1]) : 10;
      return { width: text.length * size * 0.55 };
    },
    fillText(text, x, y) {
      fillTextCalls.push({ text, x, y, font: base.font });
    },
    createLinearGradient()  { return { addColorStop() {} }; },
    createRadialGradient()  { return { addColorStop() {} }; },
    getWindowExtent: undefined,
    _fillTextCalls: fillTextCalls,
  };
  return new Proxy(base, {
    get(target, prop) {
      if (prop in target) return target[prop];
      // Any other canvas method call this gauge code makes (fillRect,
      // strokeRect, beginPath, moveTo, lineTo, arc, stroke, fill, save,
      // restore, clip, translate, rotate, setLineDash, …) — harmless no-op.
      return () => {};
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  });
}

// ── Mock factories ─────────────────────────────────────────────────────────────

/**
 * Create a Router mock that captures registered pages.
 * Assign to globalThis.Router before calling loadPage().
 */
export function makeRouter() {
  const pages = {};
  return {
    register: (name, mod) => { pages[name] = mod; },
    navigate:  vi.fn(),
    getPage:   (name) => pages[name],
  };
}

/**
 * Create an API mock with safe async defaults.
 * Pass `overrides` to replace specific methods per test.
 */
export function makeAPI(overrides = {}) {
  return {
    on:                vi.fn(() => () => {}),
    getConfig:         vi.fn(async () => ({
      export_path: '',
      all_telemetry_paths: [],
      offsets: {},
      bike_overrides: {},
      overlay: { is_bike: false, theme: 'Dark', gauges: [] },
      linked_camera_folders: [],
      speed_unit: 'auto',
    })),
    getOverlay:        vi.fn(async () => ({ is_bike: false, theme: 'Dark', gauges: [] })),
    getLaps:           vi.fn(async () => []),
    scanSessions:      vi.fn(async () => []),
    scanAllSessions:   vi.fn(async () => []),
    saveConfig:        vi.fn(async () => null),
    startExport:       vi.fn(async () => null),
    cancelExport:      vi.fn(async () => null),
    confirmClearQueue: vi.fn(async () => true),
    openFolderDialog:  vi.fn(async () => null),
    getSessionMeta:    vi.fn(async () => ({ track: '', laps: '', best: '', best_secs: null })),
    getVideoServerPort: vi.fn(async () => 0),
    saveSessionsCache:  vi.fn(async () => null),
    linkCameraFolder:   vi.fn(async () => ({ offset_seconds: 0, matched_count: 0, total_groups: 0, total_sessions: 0 })),
    unlinkCameraFolder: vi.fn(async () => null),
    ...overrides,
  };
}

// ── DOM helpers ───────────────────────────────────────────────────────────────

/** Create and attach a container div; pass it to page.mount(). */
export function makeContainer() {
  const div = document.createElement('div');
  document.body.appendChild(div);
  return div;
}

/** Remove a container from the DOM after a test. */
export function cleanupContainer(div) {
  if (div?.parentNode) div.parentNode.removeChild(div);
}

/** Flush all pending microtasks and a single macrotask tick. */
export function flushAsync() {
  return new Promise(r => setTimeout(r, 0));
}
