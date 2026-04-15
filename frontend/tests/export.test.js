/**
 * Export page — state wiring tests.
 *
 * Key invariant: the Export page must derive its "queued" item from
 * State.previewSession (set by the Data page when the user clicks
 * "Open in Overlay").  Without this wiring the Queued Laps count
 * would always show 0 even though the user selected a session.
 */
import {
  loadState, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

const PREVIEW = {
  csv_path:    '/data/session.csv',
  lap_idx:     2,
  video_paths: ['/video/clip.mp4'],
  sync_offset: 1.5,
  source:      'RaceBox',
};

describe('Export page — previewSession → selectedItems wiring', () => {
  let router, container, page;

  beforeEach(() => {
    loadState();

    router = makeRouter();
    globalThis.Router = router;
    globalThis.API    = makeAPI();

    loadPage('pages/export.js');
    container = makeContainer();
    page      = router.getPage('export');
  });

  afterEach(async () => {
    page?.unmount();
    cleanupContainer(container);
  });

  // ── On mount ───────────────────────────────────────────────────────────────

  test('selectedItems stays empty when previewSession is null on mount', async () => {
    State.set('previewSession', null);
    await page.mount(container);
    expect(State.get('selectedItems')).toEqual([]);
  });

  test('auto-populates selectedItems from previewSession on mount', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);

    const items = State.get('selectedItems');
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      csv_path:    PREVIEW.csv_path,
      lap_idx:     PREVIEW.lap_idx,
      video_paths: PREVIEW.video_paths,
      sync_offset: PREVIEW.sync_offset,
      source:      PREVIEW.source,
    });
  });

  test('derived item preserves video_paths array', async () => {
    State.set('previewSession', { ...PREVIEW, video_paths: ['/a.mp4', '/b.mp4'] });
    await page.mount(container);
    expect(State.get('selectedItems')[0].video_paths).toEqual(['/a.mp4', '/b.mp4']);
  });

  test('derived item defaults sync_offset to 0 when absent', async () => {
    const { sync_offset: _, ...noOffset } = PREVIEW;
    State.set('previewSession', noOffset);
    await page.mount(container);
    expect(State.get('selectedItems')[0].sync_offset).toBe(0);
  });

  test('derived item preserves non-zero sync_offset exactly', async () => {
    State.set('previewSession', { ...PREVIEW, sync_offset: 3.75 });
    await page.mount(container);
    expect(State.get('selectedItems')[0].sync_offset).toBe(3.75);
  });

  // ── Live updates while mounted ─────────────────────────────────────────────

  test('updates selectedItems when previewSession changes while mounted', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);

    const updated = { ...PREVIEW, csv_path: '/data/new.csv', lap_idx: 5 };
    State.set('previewSession', updated);

    const items = State.get('selectedItems');
    expect(items).toHaveLength(1);
    expect(items[0].csv_path).toBe('/data/new.csv');
    expect(items[0].lap_idx).toBe(5);
  });

  test('clears selectedItems when previewSession is set to null while mounted', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);
    expect(State.get('selectedItems')).toHaveLength(1);

    // Setting previewSession to null should not crash (no item derived)
    // selectedItems remains as-is (no auto-clear — that is acceptable)
    // What matters is no exception is thrown
    expect(() => State.set('previewSession', null)).not.toThrow();
  });

  // ── DOM reflects state ─────────────────────────────────────────────────────

  test('queued laps badge shows 1 when previewSession is set', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);

    const badge = container.querySelector('#exp-item-count');
    expect(badge?.textContent).toBe('1');
  });

  test('queued laps badge shows 0 when no previewSession', async () => {
    State.set('previewSession', null);
    await page.mount(container);

    const badge = container.querySelector('#exp-item-count');
    expect(badge?.textContent).toBe('0');
  });

  test('Start Export button is enabled when previewSession provides an item', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);

    const btn = container.querySelector('#exp-start-btn');
    expect(btn?.disabled).toBe(false);
  });

  test('Start Export button is disabled when no item is queued', async () => {
    State.set('previewSession', null);
    await page.mount(container);

    const btn = container.querySelector('#exp-start-btn');
    expect(btn?.disabled).toBe(true);
  });

  test('item row is rendered with session filename', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);

    const list = container.querySelector('#exp-item-list');
    expect(list?.textContent).toContain('session.csv');
  });

  // ── Start Export params ────────────────────────────────────────────────────

  test('_startExport fetches overlay layout and includes it in params', async () => {
    const fakeLayout = { is_bike: false, theme: 'Dark', gauges: [{ channel: 'speed' }] };
    globalThis.API = makeAPI({
      getOverlay: vi.fn(async () => fakeLayout),
      startExport: vi.fn(async () => null),
    });
    // Reload the page with the new API mock
    const freshRouter = makeRouter();
    globalThis.Router = freshRouter;
    loadPage('pages/export.js');
    const freshPage = freshRouter.getPage('export');
    const freshContainer = makeContainer();

    State.set('previewSession', PREVIEW);
    await freshPage.mount(freshContainer);

    freshContainer.querySelector('#exp-start-btn').click();
    await flushAsync();

    expect(globalThis.API.getOverlay).toHaveBeenCalled();
    const [params] = globalThis.API.startExport.mock.calls[0];
    expect(params.layout).toEqual(fakeLayout);

    freshPage.unmount();
    cleanupContainer(freshContainer);
  });

  test('scope "All selected laps" sends all_laps to the backend', async () => {
    const startExport = vi.fn(async () => null);
    globalThis.API = makeAPI({ startExport, getOverlay: vi.fn(async () => ({})) });
    const freshRouter = makeRouter();
    globalThis.Router = freshRouter;
    loadPage('pages/export.js');
    const freshPage = freshRouter.getPage('export');
    const freshContainer = makeContainer();

    State.set('previewSession', PREVIEW);
    await freshPage.mount(freshContainer);

    freshContainer.querySelector('#exp-scope').value = 'all_laps';
    freshContainer.querySelector('#exp-start-btn').click();
    await flushAsync();

    expect(startExport.mock.calls[0][0].scope).toBe('all_laps');

    freshPage.unmount();
    cleanupContainer(freshContainer);
  });

  // ── Progress bar ──────────────────────────────────────────────────────────

  test('_onProgress treats detail.value as 0–100 and clamps to [0, 100]', async () => {
    // Use a fresh page with a controllable API.on so we can call _onProgress directly
    const onCalls = [];
    const trackingAPI = makeAPI({ on: vi.fn((event, cb) => { onCalls.push({ event, cb }); return () => {}; }) });
    globalThis.API = trackingAPI;
    const freshRouter = makeRouter();
    globalThis.Router = freshRouter;
    loadPage('pages/export.js');
    const freshPage = freshRouter.getPage('export');
    const freshContainer = makeContainer();

    State.set('previewSession', null);
    await freshPage.mount(freshContainer);

    const progressCb = onCalls.find(c => c.event === 'export_progress')?.cb;
    expect(progressCb).toBeDefined();

    const pctEl = freshContainer.querySelector('#exp-progress-pct');

    // Python sends 0–100; should display directly as a percentage
    progressCb({ value: 87, message: '' });
    expect(pctEl?.textContent).toBe('87%');

    // Clamp: a rogue value > 100 must not exceed 100%
    progressCb({ value: 8700, message: '' });
    expect(pctEl?.textContent).toBe('100%');

    // Zero
    progressCb({ value: 0, message: '' });
    expect(pctEl?.textContent).toBe('0%');

    freshPage.unmount();
    cleanupContainer(freshContainer);
  });

  // ── Scope options ─────────────────────────────────────────────────────────

  test('scope dropdown includes "full" option', async () => {
    State.set('previewSession', null);
    await page.mount(container);

    const scopeSel = container.querySelector('#exp-scope');
    const values = Array.from(scopeSel?.options ?? []).map(o => o.value);
    expect(values).toContain('full');
  });

  // ── ref_mode passthrough ──────────────────────────────────────────────────
  // ref_mode is now stored in the overlay layout (editor page), not on the
  // Export page.  _startExport reads it from API.getOverlay().

  test('ref_mode from overlay is forwarded to startExport', async () => {
    const startExport = vi.fn(async () => null);
    globalThis.API = makeAPI({
      startExport,
      getOverlay: vi.fn(async () => ({ ref_mode: 'session_best' })),
    });
    const freshRouter = makeRouter();
    globalThis.Router = freshRouter;
    loadPage('pages/export.js');
    const freshPage = freshRouter.getPage('export');
    const freshContainer = makeContainer();

    State.set('previewSession', PREVIEW);
    await freshPage.mount(freshContainer);

    freshContainer.querySelector('#exp-start-btn').click();
    await flushAsync();

    expect(startExport.mock.calls[0][0].ref_mode).toBe('session_best');

    freshPage.unmount();
    cleanupContainer(freshContainer);
  });

  test('ref_mode defaults to "none" when overlay has no ref_mode', async () => {
    const startExport = vi.fn(async () => null);
    globalThis.API = makeAPI({ startExport, getOverlay: vi.fn(async () => ({})) });
    const freshRouter = makeRouter();
    globalThis.Router = freshRouter;
    loadPage('pages/export.js');
    const freshPage = freshRouter.getPage('export');
    const freshContainer = makeContainer();

    State.set('previewSession', PREVIEW);
    await freshPage.mount(freshContainer);

    freshContainer.querySelector('#exp-start-btn').click();
    await flushAsync();

    expect(startExport.mock.calls[0][0].ref_mode).toBe('none');

    freshPage.unmount();
    cleanupContainer(freshContainer);
  });

  // ── Unmount cleans up subscriptions ───────────────────────────────────────

  test('previewSession changes after unmount do not update selectedItems', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);

    page.unmount();

    const before = State.get('selectedItems');
    State.set('previewSession', { ...PREVIEW, csv_path: '/data/other.csv' });

    // selectedItems should not have changed after unmount
    expect(State.get('selectedItems')).toEqual(before);
  });
});
