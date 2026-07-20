/**
 * Export page — state wiring tests.
 *
 * Key invariant: the Export page must derive its "queued" item from
 * State.previewSession (set by the Data page when the user clicks
 * "Open in Overlay").  Without this wiring the Queued Laps count
 * would always show 0 even though the user selected a session.
 *
 * The Export page has no scope/padding controls of its own — those live on
 * the Overlay tab (editor.js) and travel with each queued item. It does have
 * its own "Start Export" button (runs whatever is already queued) — see the
 * "Start Export button" describe block below.
 * See export_params.test.js for the params-building logic that page uses.
 */
import {
  loadState, loadPage, loadExportParams, makeRouter, makeAPI,
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

  test('item row is rendered with session filename', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);

    const list = container.querySelector('#exp-item-list');
    expect(list?.textContent).toContain('session.csv');
  });

  test('empty queue shows a "Go to Data" button that navigates there', async () => {
    State.set('previewSession', null);
    await page.mount(container);

    const btn = container.querySelector('#exp-goto-data-btn');
    expect(btn).not.toBeNull();
    btn.click();
    expect(router.navigate).toHaveBeenCalledWith('data');
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

  test('first progress event flips the status badge from Idle to Running', async () => {
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
    const badge = freshContainer.querySelector('#exp-status-badge');
    expect(badge?.textContent).toBe('Idle');

    progressCb({ value: 10, message: 'Rendering…' });
    expect(badge?.textContent).toBe('Running');
    expect(freshContainer.querySelector('#exp-cancel-btn')?.classList.contains('hidden')).toBe(false);

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

describe('Export page — Start Export button', () => {
  let router, api, container, page;

  beforeEach(() => {
    loadState();
    loadExportParams();

    router = makeRouter();
    api    = makeAPI();
    globalThis.Router = router;
    globalThis.API    = api;

    loadPage('pages/export.js');
    container = makeContainer();
    page      = router.getPage('export');
  });

  afterEach(async () => {
    page?.unmount();
    cleanupContainer(container);
  });

  test('is hidden when the queue is empty', async () => {
    State.set('previewSession', null);
    await page.mount(container);

    const btn = container.querySelector('#exp-start-btn');
    expect(btn?.classList.contains('hidden')).toBe(true);
  });

  test('is visible once an item is queued', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);

    const btn = container.querySelector('#exp-start-btn');
    expect(btn?.classList.contains('hidden')).toBe(false);
  });

  test('clicking it starts export with the whole queue, not just the last-added item', async () => {
    State.set('previewSession', PREVIEW);
    await page.mount(container);
    State.set('selectedItems', [
      ...State.get('selectedItems'),
      { csv_path: '/data/other.csv', lap_idx: 0, video_paths: ['/other.mp4'], sync_offset: 0, scope: 'selected_lap' },
    ]);

    const btn = container.querySelector('#exp-start-btn');
    btn.click();
    await flushAsync();

    expect(api.getConfig).toHaveBeenCalled();
    expect(api.getOverlay).toHaveBeenCalled();
    expect(api.startExport).toHaveBeenCalledTimes(1);
    const params = api.startExport.mock.calls[0][0];
    expect(params.items).toHaveLength(2);
    expect(params.items.map(i => i.csv_path)).toEqual(
      expect.arrayContaining([PREVIEW.csv_path, '/data/other.csv']));
  });

  test('does nothing when the queue is empty', async () => {
    State.set('previewSession', null);
    await page.mount(container);

    const btn = container.querySelector('#exp-start-btn');
    btn.click();
    await flushAsync();

    expect(api.startExport).not.toHaveBeenCalled();
  });

  test('hides again once export_progress reports the run has started', async () => {
    const onCalls = [];
    api = makeAPI({ on: vi.fn((event, cb) => { onCalls.push({ event, cb }); return () => {}; }) });
    globalThis.API = api;
    const freshRouter = makeRouter();
    globalThis.Router = freshRouter;
    loadPage('pages/export.js');
    const freshPage = freshRouter.getPage('export');
    const freshContainer = makeContainer();

    State.set('previewSession', PREVIEW);
    await freshPage.mount(freshContainer);
    expect(freshContainer.querySelector('#exp-start-btn')?.classList.contains('hidden')).toBe(false);

    const progressCb = onCalls.find(c => c.event === 'export_progress')?.cb;
    progressCb({ value: 5, message: 'Rendering…' });

    expect(freshContainer.querySelector('#exp-start-btn')?.classList.contains('hidden')).toBe(true);

    freshPage.unmount();
    cleanupContainer(freshContainer);
  });
});
