/**
 * Data page — "Lap 1 start" contract (JS side of tests/test_lap1_start_contract.py).
 *
 *   video_time(lap 1) = sync_offset + elapsed_start(first non-outlap lap)
 *   Mark:  sync_offset = video.currentTime - elapsed_start(first non-outlap lap)
 *
 * Both depend on the lap list from getLaps() being present. These tests pin:
 *   - the auto-seek lands on lap 1, not on the session start,
 *   - Mark stores the offset relative to the session start (outlap subtracted),
 *   - Mark clicked before getLaps() has answered still subtracts the outlap
 *     (it used to silently save the raw video time — off by the whole outlap),
 *   - another session's auto-sync result does not rebuild the pane (and the
 *     <video>) the user is currently scrubbing.
 */
import {
  loadState, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

const CSV   = '/data/2026-08-20/session.csv';
const OTHER = '/data/2026-08-20/other.csv';

const SESSION = {
  csv_path: CSV, video_paths: ['/video/DJI_0702_001.MP4'], matched: true,
  sync_offset: 2.285, sync_source: 'user', source: 'RaceBox',
  csv_start: '2026-08-20T08:48:00Z', is_bike: false, needs_conversion: false,
};
const OTHER_SESSION = {
  ...SESSION, csv_path: OTHER, video_paths: ['/video/DJI_0704_001.MP4'],
  sync_offset: null, sync_source: null, csv_start: '2026-08-20T09:37:00Z',
};

// Real shape returned by WebviewAPI.get_laps for a RaceBox session: lap 0 is
// the outlap and lap 1 starts 16.88 s into the telemetry.
const OUTLAP_DUR = 16.88;
const LAPS = [
  { lap_idx: 0, lap_num: 0, duration: 638.84, is_best: false, elapsed_start: 0,          is_outlap: true,  is_inlap: false },
  { lap_idx: 1, lap_num: 1, duration: 63.2,   is_best: false, elapsed_start: OUTLAP_DUR, is_outlap: false, is_inlap: false },
  { lap_idx: 2, lap_num: 2, duration: 62.1,   is_best: true,  elapsed_start: 80.08,      is_outlap: false, is_inlap: false },
];

const VIDEO_DURATION = 409.376;

function makeVideoReady(video) {
  Object.defineProperty(video, 'duration',   { configurable: true, value: VIDEO_DURATION });
  Object.defineProperty(video, 'readyState', { configurable: true, value: 1 });
}

describe('Data page — Lap 1 start / sync offset contract', () => {
  let router, container, page, api, handlers, resolveLaps, lapsDeferred;

  beforeEach(async () => {
    loadState();
    // jsdom's media element has no real loader; keep its "not implemented"
    // noise out of the test output.
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});

    handlers = {};
    lapsDeferred = new Promise(r => { resolveLaps = r; });

    router = makeRouter();
    globalThis.Router = router;
    api = makeAPI({
      on: vi.fn((evt, fn) => { handlers[evt] = fn; return () => {}; }),
      getConfig: vi.fn(async () => ({
        all_telemetry_paths: ['/data'],
        offsets:             { [CSV]: SESSION.sync_offset },
        offset_sources:      { [CSV]: 'user' },
        bike_overrides:      {},
        overlay:             { is_bike: false, theme: 'Dark', gauges: [] },
        auto_sync_enabled:   false,
      })),
      scanSessions:    vi.fn(async () => [SESSION, OTHER_SESSION]),
      scanAllSessions: vi.fn(async () => [SESSION, OTHER_SESSION]),
      getLaps:         vi.fn(() => lapsDeferred),
      getSessionMeta:  vi.fn(async () => ({ track: 'Genk', laps: '10', best: '1:02.100', best_secs: 62.1 })),
    });
    globalThis.API = api;

    loadPage('pages/data.js');
    container = makeContainer();
    page      = router.getPage('data');
    await page.mount(container);
    await flushAsync();
    await flushAsync();
  });

  afterEach(() => {
    page?.unmount();
    cleanupContainer(container);
    vi.restoreAllMocks();
  });

  function selectSession() {
    container.querySelector(`.dl-row[data-csv="${CSV}"]`).click();
  }

  async function selectSessionWithLaps() {
    selectSession();
    resolveLaps(LAPS);
    await flushAsync();
    await flushAsync();
  }

  test('auto-seek lands on lap 1 = sync_offset + first timed lap elapsed_start', async () => {
    await selectSessionWithLaps();
    const video = container.querySelector('#sync-video');
    expect(video).not.toBeNull();

    makeVideoReady(video);
    video.dispatchEvent(new Event('loadedmetadata'));

    expect(video.currentTime).toBeCloseTo(SESSION.sync_offset + OUTLAP_DUR, 3);
    expect(container.querySelector('#sv-time').textContent).toBe('0:19.165');
  });

  test('auto-seek does not fire before laps are known (would land on session start)', async () => {
    selectSession();                       // getLaps still pending
    const video = container.querySelector('#sync-video');
    makeVideoReady(video);
    video.dispatchEvent(new Event('loadedmetadata'));
    expect(video.currentTime).toBe(0);     // untouched — not sync_offset + 0

    resolveLaps(LAPS);
    await flushAsync();
    await flushAsync();
    // loadLaps() re-rendered the pane; the fresh <video> seeks once ready.
    const video2 = container.querySelector('#sync-video');
    expect(video2).not.toBe(video);
    makeVideoReady(video2);
    video2.dispatchEvent(new Event('loadedmetadata'));
    expect(video2.currentTime).toBeCloseTo(SESSION.sync_offset + OUTLAP_DUR, 3);
  });

  test('Mark stores video time minus the outlap duration', async () => {
    await selectSessionWithLaps();
    const video = container.querySelector('#sync-video');
    makeVideoReady(video);
    video.currentTime = 30.0;               // user scrubbed to where lap 1 starts

    container.querySelector('#sv-mark').click();
    await flushAsync();
    await flushAsync();

    expect(api.saveConfig).toHaveBeenCalledTimes(1);
    const saved = api.saveConfig.mock.calls[0][0];
    expect(saved.offsets[CSV]).toBeCloseTo(30.0 - OUTLAP_DUR, 6);
    expect(saved.offset_sources[CSV]).toBe('user');
    expect(State.get('previewSession').sync_offset).toBeCloseTo(30.0 - OUTLAP_DUR, 6);
  });

  test('Mark clicked before getLaps() answers still subtracts the outlap', async () => {
    selectSession();                        // laps pending
    const video = container.querySelector('#sync-video');
    makeVideoReady(video);
    video.currentTime = 30.0;

    container.querySelector('#sv-mark').click();
    await flushAsync();
    expect(api.saveConfig).not.toHaveBeenCalled();   // waiting for lap data

    resolveLaps(LAPS);
    await flushAsync();
    await flushAsync();

    expect(api.saveConfig).toHaveBeenCalledTimes(1);
    expect(api.saveConfig.mock.calls[0][0].offsets[CSV]).toBeCloseTo(30.0 - OUTLAP_DUR, 6);
  });

  test('Mark refuses to save when lap data cannot be loaded at all', async () => {
    // get_laps returns [] when the session fails to load. Treating that as
    // "outlap = 0" would store an offset short by the whole outlap.
    api.getLaps.mockImplementation(async () => []);
    selectSession();
    resolveLaps([]);
    await flushAsync();
    await flushAsync();

    const video = container.querySelector('#sync-video');
    makeVideoReady(video);
    video.currentTime = 30.0;

    container.querySelector('#sv-mark').click();
    await flushAsync();
    await flushAsync();

    expect(api.saveConfig).not.toHaveBeenCalled();
    expect(container.querySelector('#sv-mark-val').textContent).toMatch(/unavailable/i);
  });

  test("another session's auto-sync result leaves the pane being aligned alone", async () => {
    await selectSessionWithLaps();
    const video = container.querySelector('#sync-video');
    makeVideoReady(video);
    video.currentTime = 25.0;               // mid-scrub

    handlers['auto_sync_progress']({ csv_path: OTHER, status: 'done', offset: 4.2, confidence: 7.1 });
    handlers['auto_sync_progress']({ csv_path: OTHER, status: 'failed', confidence: 2.0 });

    expect(container.querySelector('#sync-video')).toBe(video);
    expect(video.currentTime).toBe(25.0);
    // A late auto result for the selected session itself must not override
    // the offset the user already confirmed either.
    handlers['auto_sync_progress']({ csv_path: CSV, status: 'done', offset: 4.2, confidence: 7.1 });
    expect(container.querySelector('#sync-video')).toBe(video);
    expect(api.saveConfig).not.toHaveBeenCalled();
  });
});
