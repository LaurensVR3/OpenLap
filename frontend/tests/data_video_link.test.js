/**
 * Data page — undoing a sync offset and a manual video link.
 *
 * Both are one-way without these controls: save_config() merges its dict
 * fields so an offset can be overwritten but never removed, and a video
 * assigned by hand stays assigned. Backend halves are covered by
 * tests/test_webview_api.py (TestClearOffset / TestUnassignVideo).
 */
import {
  loadState, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

const CSV = '/data/2026-08-20/session.csv';

const BASE_SESSION = {
  csv_path: CSV, video_paths: ['/video/DJI_0702_001.MP4'], matched: true,
  source: 'RaceBox', csv_start: '2026-08-20T08:48:00Z',
  is_bike: false, needs_conversion: false,
};

const LAPS = [
  { lap_idx: 0, lap_num: 0, duration: 638.8, is_best: false, elapsed_start: 0,     is_outlap: true,  is_inlap: false },
  { lap_idx: 1, lap_num: 1, duration: 63.2,  is_best: true,  elapsed_start: 16.88, is_outlap: false, is_inlap: false },
];

async function mountWith(sessionOverrides, configOverrides = {}) {
  loadState();
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});

  const session = { ...BASE_SESSION, ...sessionOverrides };
  const router  = makeRouter();
  globalThis.Router = router;
  const api = makeAPI({
    getConfig: vi.fn(async () => ({
      all_telemetry_paths: ['/data'],
      offsets:        session.sync_offset != null ? { [CSV]: session.sync_offset } : {},
      offset_sources: session.sync_source ? { [CSV]: session.sync_source } : {},
      bike_overrides: {},
      overlay: { is_bike: false, theme: 'Dark', gauges: [] },
      auto_sync_enabled: false,
      ...configOverrides,
    })),
    scanSessions:    vi.fn(async () => [session]),
    scanAllSessions: vi.fn(async () => [session]),
    getLaps:         vi.fn(async () => LAPS),
    getSessionMeta:  vi.fn(async () => ({ track: 'Genk', laps: '9', best: '1:02.100', best_secs: 62.1 })),
  });
  globalThis.API = api;

  loadPage('pages/data.js');
  const container = makeContainer();
  const page = router.getPage('data');
  await page.mount(container);
  await flushAsync();
  await flushAsync();
  container.querySelector(`.dl-row[data-csv="${CSV}"]`).click();
  await flushAsync();
  await flushAsync();
  return { api, container, page, session };
}

describe('Data page — unset offset', () => {
  let ctx;
  afterEach(() => {
    ctx?.page?.unmount();
    cleanupContainer(ctx?.container);
    vi.restoreAllMocks();
  });

  test('the button is offered only once an offset exists', async () => {
    ctx = await mountWith({ sync_offset: null, sync_source: null });
    expect(ctx.container.querySelector('#sv-unset')).toBeNull();

    ctx.page.unmount();
    cleanupContainer(ctx.container);
    ctx = await mountWith({ sync_offset: 2.285, sync_source: 'user' });
    expect(ctx.container.querySelector('#sv-unset')).not.toBeNull();
  });

  test('clearing calls the backend and drops the offset from the UI', async () => {
    ctx = await mountWith({ sync_offset: 2.285, sync_source: 'user' });

    ctx.container.querySelector('#sv-unset').click();
    await flushAsync();
    await flushAsync();

    expect(ctx.api.clearOffset).toHaveBeenCalledWith(CSV);
    expect(ctx.container.querySelector('#dr-off-display').textContent.trim()).toBe('not set');
    expect(ctx.container.querySelector('#sv-unset')).toBeNull();
    // Session row badge goes back to unsynced too.
    expect(ctx.container.querySelector(`.dl-row[data-csv="${CSV}"] .dl-sync`).textContent)
      .toContain('unset');
  });

  test('an offset can still be cleared when the video has gone missing', async () => {
    // Videos live on a NAS; when it is offline the session shows "no video"
    // and the align card is not rendered at all. The offset is still stored,
    // so the way to clear it must not live inside that card.
    ctx = await mountWith({ video_paths: [], matched: false,
                            sync_offset: 2.285, sync_source: 'user' });
    expect(ctx.container.querySelector('#sync-video')).toBeNull();

    const unset = ctx.container.querySelector('#sv-unset');
    expect(unset).not.toBeNull();
    unset.click();
    await flushAsync();
    await flushAsync();

    expect(ctx.api.clearOffset).toHaveBeenCalledWith(CSV);
    expect(ctx.container.querySelector('#dr-off-display').textContent.trim()).toBe('not set');
  });
});

describe('Data page — unlink a manually assigned video', () => {
  let ctx;
  afterEach(() => {
    ctx?.page?.unmount();
    cleanupContainer(ctx?.container);
    vi.restoreAllMocks();
  });

  test('the button is offered only for a hand-assigned video', async () => {
    ctx = await mountWith({ sync_offset: 1.0, video_override: false });
    expect(ctx.container.querySelector('#sv-unlink-vid')).toBeNull();

    ctx.page.unmount();
    cleanupContainer(ctx.container);
    ctx = await mountWith({ sync_offset: 1.0, video_override: true });
    expect(ctx.container.querySelector('#sv-unlink-vid')).not.toBeNull();
  });

  test('unlinking drops the video, clears the offset, and persists both', async () => {
    ctx = await mountWith({ sync_offset: 1.0, sync_source: 'user', video_override: true });

    ctx.container.querySelector('#sv-unlink-vid').click();
    await flushAsync();
    await flushAsync();

    expect(ctx.api.unassignVideo).toHaveBeenCalledWith(CSV);
    // The offset described a position in the video that is no longer attached.
    expect(ctx.api.clearOffset).toHaveBeenCalledWith(CSV);
    // Cache re-saved without the video, so a reload does not resurrect it.
    expect(ctx.api.saveSessionsCache).toHaveBeenCalled();
    const cached = ctx.api.saveSessionsCache.mock.calls.at(-1)[0]
      .find(s => s.csv_path === CSV);
    expect(cached.video_paths).toEqual([]);
    expect(cached.matched).toBe(false);

    // Panel falls back to the "no video" state.
    expect(ctx.container.querySelector('#dr-assign-vid-btn')).not.toBeNull();
    expect(ctx.container.querySelector('#sync-video')).toBeNull();
    expect(State.get('previewSession').video_paths).toEqual([]);
  });

  test('assigning a video offers the unlink button straight away', async () => {
    ctx = await mountWith({ video_paths: [], matched: false, sync_offset: null });
    ctx.api.openFileDialog = vi.fn(async () => '/video/manual.MP4');

    ctx.container.querySelector('#dr-assign-vid-btn').click();
    await flushAsync();
    await flushAsync();

    expect(ctx.api.assignVideo).toHaveBeenCalledWith(CSV, '/video/manual.MP4');
    expect(ctx.container.querySelector('#sv-unlink-vid')).not.toBeNull();
  });
});
