/**
 * Overlay editor — export bar tests.
 *
 * Regression coverage for the export controls: scope/padding/overlay-only
 * and the "+ Add to Queue" / "▶ Export Now" actions are always visible on
 * the Overlay tab (not hidden behind a menu) — this is the only place those
 * settings exist now, since the Export tab itself has no configuration
 * controls of its own (see export.js).
 */
import {
  loadState, loadExportParams, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

const SESSION = {
  csv_path:    '/data/session.csv',
  video_paths: ['/video/clip.mp4'],
  sync_offset: 1.5,
  source:      'RaceBox',
  csv_start:   '2024-06-15T14:32:00Z',
};

const LAPS = [
  { lap_idx: 0, lap_num: 1, duration: 84.3, is_best: false, elapsed_start: 0, is_outlap: true },
  { lap_idx: 1, lap_num: 2, duration: 83.1, is_best: true,  elapsed_start: 84.3 },
];

describe('Overlay editor — export bar', () => {
  let router, container, page;

  beforeEach(async () => {
    loadState();

    // jsdom has no ResizeObserver / canvas 2D context — editor.js uses both
    // to size gauge previews. Stub just enough that mount() doesn't throw.
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    const fakeCtx = {
      clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
      stroke() {}, fill() {}, save() {}, restore() {}, arc() {}, closePath() {},
      measureText: () => ({ width: 10 }), roundRect() {},
    };
    HTMLCanvasElement.prototype.getContext = () => fakeCtx;

    router = makeRouter();
    globalThis.Router = router;
    globalThis.API = makeAPI({
      getVideoServerPort: vi.fn(async () => 0),
      getConfig:          vi.fn(async () => ({ overlay: { is_bike: false, theme: 'Dark', gauges: [] } })),
      getOverlay:         vi.fn(async () => ({ is_bike: false, theme: 'Dark', gauges: [] })),
      listPresets:        vi.fn(async () => ({})),
      getSessionMeta:     vi.fn(async () => ({ track: 'Spa-Francorchamps' })),
      getLaps:            vi.fn(async () => LAPS),
      loadLapHistory:     vi.fn(async () => []),
      getTrackMapGeometry: vi.fn(async () => ({ lats: [], lons: [] })),
    });

    // editor.js's export bar calls ExportParams.buildExportParams() — loaded
    // as a real (non-IIFE) global script in index.html, same as gauges/base.js.
    loadExportParams();
    loadPage('pages/editor.js');
    container = makeContainer();
    page      = router.getPage('editor');

    State.set('previewSession', { ...SESSION, lap_idx: 1 });
    await page.mount(container);
    await flushAsync();
    await flushAsync();
  });

  afterEach(() => {
    page?.unmount();
    cleanupContainer(container);
  });

  test('scope select, padding input, overlay-only checkbox, and both action buttons are visible without any extra click', () => {
    expect(container.querySelector('#exp-scope-sel')).not.toBeNull();
    expect(container.querySelector('#exp-padding-inp')).not.toBeNull();
    expect(container.querySelector('#exp-overlay-only-chk')).not.toBeNull();
    expect(container.querySelector('#exp-queue-btn')).not.toBeNull();
    expect(container.querySelector('#exp-now-btn')).not.toBeNull();
  });

  test('lap range picker is hidden unless scope is "Lap range"', () => {
    expect(container.querySelector('#exp-range-wrap').style.display).toBe('none');

    const scopeSel = container.querySelector('#exp-scope-sel');
    scopeSel.value = 'lap_range';
    scopeSel.dispatchEvent(new Event('change'));

    expect(container.querySelector('#exp-range-wrap').style.display).not.toBe('none');
  });

  test('"+ Add to Queue" adds the current lap without navigating away', () => {
    container.querySelector('#exp-queue-btn').click();

    const items = State.get('selectedItems') || [];
    expect(items).toHaveLength(1);
    expect(items[0].csv_path).toBe(SESSION.csv_path);
    expect(items[0].scope).toBe('selected_lap');
    expect(items[0].padding).toBe(5);
    expect(router.navigate).not.toHaveBeenCalled();
  });

  test('changing padding is reflected on the queued item', () => {
    const paddingInp = container.querySelector('#exp-padding-inp');
    paddingInp.value = '8';
    paddingInp.dispatchEvent(new Event('change'));

    container.querySelector('#exp-queue-btn').click();
    expect(State.get('selectedItems')[0].padding).toBe(8);
  });

  test('changing scope to "Full session" is reflected on the queued item', () => {
    const scopeSel = container.querySelector('#exp-scope-sel');
    scopeSel.value = 'full';
    scopeSel.dispatchEvent(new Event('change'));

    container.querySelector('#exp-queue-btn').click();
    expect(State.get('selectedItems')[0].scope).toBe('full');
  });

  test('checking overlay-only is reflected on the queued item', () => {
    const chk = container.querySelector('#exp-overlay-only-chk');
    chk.checked = true;
    chk.dispatchEvent(new Event('change'));

    container.querySelector('#exp-queue-btn').click();
    expect(State.get('selectedItems')[0].overlay_only).toBe(true);
  });

  test('"▶ Export Now" queues the current lap, calls startExport, and navigates to export', async () => {
    const startExport = vi.fn(async () => null);
    globalThis.API.startExport = startExport;

    container.querySelector('#exp-now-btn').click();
    await flushAsync();

    expect(startExport).toHaveBeenCalledTimes(1);
    const params = startExport.mock.calls[0][0];
    expect(params.items).toHaveLength(1);
    expect(params.items[0].csv_path).toBe(SESSION.csv_path);
    expect(router.navigate).toHaveBeenCalledWith('export');
  });

  test('document mousemove/mouseup listeners are removed on unmount, not leaked across remounts', async () => {
    // Regression coverage: setupMouseEvents() used to add mousemove/mouseup
    // listeners straight to `document` on every mount() and unmount() never
    // removed them — each Data→Overlay round trip left one more permanent
    // pair behind, sharing the same module-level drag state, so a stray
    // mouseup after N visits fired saveLayout() (API.saveOverlay) N times.
    //
    // beforeEach already performed one mount; tear that down with the
    // (currently un-spied) real listeners before wiring the spies so counts
    // below only reflect the cycles this test drives.
    page.unmount();
    cleanupContainer(container);

    const addSpy    = vi.spyOn(document, 'addEventListener');
    const removeSpy = vi.spyOn(document, 'removeEventListener');

    for (let i = 0; i < 2; i++) {
      container = makeContainer();
      await page.mount(container);
      await flushAsync();
      await flushAsync();
      page.unmount();
      cleanupContainer(container);
    }

    const addCount    = type => addSpy.mock.calls.filter(c => c[0] === type).length;
    const removeCount = type => removeSpy.mock.calls.filter(c => c[0] === type).length;

    expect(addCount('mousemove')).toBeGreaterThan(0);
    expect(addCount('mousemove')).toBe(removeCount('mousemove'));
    expect(addCount('mouseup')).toBeGreaterThan(0);
    expect(addCount('mouseup')).toBe(removeCount('mouseup'));

    // Re-mount so the outer afterEach's page.unmount()/cleanupContainer() has
    // a live container/page to tear down, matching every other test's shape.
    container = makeContainer();
    await page.mount(container);
    await flushAsync();
    await flushAsync();
  });

  test('"▶ Export Now" does nothing if nothing can be queued and no session is loaded', async () => {
    // Fresh mount with no previewSession at all
    page.unmount();
    cleanupContainer(container);
    State.set('previewSession', null);
    State.set('selectedItems', []);

    const freshRouter = makeRouter();
    globalThis.Router = freshRouter;
    loadPage('pages/editor.js');
    const freshPage = freshRouter.getPage('editor');
    const freshContainer = makeContainer();
    await freshPage.mount(freshContainer);
    await flushAsync();

    const startExport = vi.fn(async () => null);
    globalThis.API.startExport = startExport;

    freshContainer.querySelector('#exp-now-btn').click();
    await flushAsync();

    expect(startExport).not.toHaveBeenCalled();
    expect(freshRouter.navigate).not.toHaveBeenCalled();

    freshPage.unmount();
    cleanupContainer(freshContainer);
  });
});

describe('Overlay editor — HTML-attribute escaping', () => {
  // Regression coverage: two spots interpolated network/filesystem-derived
  // values straight into HTML attributes without the file's own `_esc()`
  // helper — data-osm-id (from an OSM/Overpass network response) and the
  // manual ref-lap picker's data-csv (a raw filesystem path). A value
  // containing a `"` could break out of the attribute.
  const MALICIOUS = '123" onmouseover="alert(1)';

  let router, container, page;

  beforeEach(async () => {
    loadState();

    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    const fakeCtx = {
      clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
      stroke() {}, fill() {}, save() {}, restore() {}, arc() {}, closePath() {},
      measureText: () => ({ width: 10 }), roundRect() {}, fillText() {},
    };
    HTMLCanvasElement.prototype.getContext = () => fakeCtx;
    // gauges/map.js isn't loaded by this test (out of scope — only editor.js/
    // export.js/etc. are touched here); renderGaugeEl() catches the resulting
    // "GaugeMap is not defined" error and draws an error placeholder, which is
    // fine for these DOM-attribute-escaping tests that don't assert on canvas output.
    globalThis.GaugeMap = { render: () => {}, renderZoomed: () => {} };

    router = makeRouter();
    globalThis.Router = router;
    globalThis.API = makeAPI({
      getVideoServerPort: vi.fn(async () => 0),
      getConfig:          vi.fn(async () => ({ overlay: { is_bike: false, theme: 'Dark', gauges: [] } })),
      getOverlay:         vi.fn(async () => ({
        is_bike: false, theme: 'Dark',
        gauges: [{ channel: 'map', style: 'Circuit', visible: true, x: 0.1, y: 0.1, w: 0.3, h: 0.3 }],
      })),
      listPresets:        vi.fn(async () => ({})),
      getSessionMeta:     vi.fn(async () => ({ track: 'Spa-Francorchamps' })),
      getLaps:            vi.fn(async () => LAPS),
      loadLapHistory:     vi.fn(async () => []),
      getTrackMapGeometry: vi.fn(async () => ({ lats: [], lons: [] })),
      getTrackMapCandidates: vi.fn(async () => ({
        candidates: [{ osm_id: MALICIOUS, name: 'Spa', centroid_dist_m: 500 }],
        selected_osm_id: '', auto_osm_id: '', track_key: 'spa',
      })),
      getLapsForRefPicker: vi.fn(async () => ([
        { date: '2024-06-15T00:00:00Z', laps: [{ csv_path: MALICIOUS, lap_num: 1, duration: 80, is_best: true }] },
      ])),
    });

    loadExportParams();
    loadPage('pages/editor.js');
    container = makeContainer();
    page      = router.getPage('editor');

    State.set('previewSession', { ...SESSION, lap_idx: 1 });
    await page.mount(container);
    await flushAsync();
    await flushAsync();
  });

  afterEach(() => {
    page?.unmount();
    cleanupContainer(container);
  });

  test('OSM candidate osm_id is escaped in the data-osm-id attribute', async () => {
    const canvas = container.querySelector('.gauge-canvas[data-gauge-idx="0"]');
    expect(canvas).not.toBeNull();
    canvas.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));

    const configureBtn = container.querySelector('#map-osm-configure');
    expect(configureBtn).not.toBeNull();
    configureBtn.click();
    await flushAsync();
    await flushAsync();

    const row = container.querySelector('.osm-cand-row[data-osm-id]');
    expect(row).not.toBeNull();
    // If unescaped, the embedded `"` would break out of the attribute and
    // dataset.osmId would come back truncated (e.g. "123") with a stray
    // onmouseover attribute injected onto the element instead.
    expect(row.dataset.osmId).toBe(MALICIOUS);
    expect(row.hasAttribute('onmouseover')).toBe(false);
  });

  test('ref lap picker csv path is escaped in the data-csv attribute', async () => {
    const refSel = container.querySelector('#ref-mode-sel');
    expect(refSel).not.toBeNull();
    refSel.value = 'manual';
    refSel.dispatchEvent(new Event('change'));
    await flushAsync();
    await flushAsync();

    const row = container.querySelector('.ref-lap-row');
    expect(row).not.toBeNull();
    expect(row.dataset.csv).toBe(MALICIOUS);
    expect(row.hasAttribute('onmouseover')).toBe(false);
  });
});
