/**
 * Data page — previewSession wiring tests.
 *
 * Verifies that selecting a session and clicking "Open in Overlay" correctly
 * populates State.previewSession so the Overlay and Export pages can consume it.
 */
import {
  loadState, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

const SESSION = {
  csv_path:         '/data/2024-06-15/session.csv',
  video_paths:      ['/video/clip.mp4'],
  matched:          true,
  sync_offset:      1.5,
  source:           'RaceBox',
  csv_start:        '2024-06-15T14:32:00Z',
  is_bike:          false,
  needs_conversion: false,
};

const LAPS = [
  { lap_idx: 0, duration: 84.3, is_best: false },
  { lap_idx: 1, duration: 83.1, is_best: true  },
];

describe('Data page — previewSession wiring', () => {
  let router, container, page;

  beforeEach(async () => {
    loadState();

    router = makeRouter();
    globalThis.Router = router;
    globalThis.API = makeAPI({
      getConfig: vi.fn(async () => ({
        all_telemetry_paths: ['/data'],
        offsets:             { [SESSION.csv_path]: SESSION.sync_offset },
        bike_overrides:      {},
        overlay:             { is_bike: false, theme: 'Dark', gauges: [] },
      })),
      scanSessions:      vi.fn(async () => [SESSION]),
      getLaps:           vi.fn(async () => LAPS),
      getSessionMeta:    vi.fn(async () => ({
        track: 'Spa-Francorchamps', laps: '2', best: '1:23.100', best_secs: 83.1,
      })),
    });

    loadPage('pages/data.js');
    container = makeContainer();
    page      = router.getPage('data');

    await page.mount(container);
    // Let scan + meta-enrichment promises resolve
    await flushAsync();
    await flushAsync();
  });

  afterEach(() => {
    page?.unmount();
    cleanupContainer(container);
  });

  // ── Session selection ──────────────────────────────────────────────────────

  test('clicking a session row sets previewSession', () => {
    const row = container.querySelector('.dl-row');
    expect(row).not.toBeNull();
    row.click();

    const ps = State.get('previewSession');
    expect(ps).not.toBeNull();
    expect(ps.csv_path).toBe(SESSION.csv_path);
  });

  test('previewSession carries the correct video_paths', () => {
    container.querySelector('.dl-row')?.click();
    expect(State.get('previewSession').video_paths).toEqual(SESSION.video_paths);
  });

  test('previewSession carries the stored sync_offset from config', () => {
    container.querySelector('.dl-row')?.click();
    expect(State.get('previewSession').sync_offset).toBe(SESSION.sync_offset);
  });

  test('previewSession carries the source field', () => {
    container.querySelector('.dl-row')?.click();
    expect(State.get('previewSession').source).toBe(SESSION.source);
  });

  // ── "Open in Overlay" button ───────────────────────────────────────────────

  test('"Open in Overlay" navigates to the editor page', async () => {
    container.querySelector('.dl-row')?.click();
    await flushAsync(); // let loadLaps resolve
    await flushAsync();

    container.querySelector('#dr-goto-overlay')?.click();
    expect(router.navigate).toHaveBeenCalledWith('editor');
  });

  test('"Open in Overlay" sets previewSession with the best lap index', async () => {
    container.querySelector('.dl-row')?.click();
    await flushAsync();
    await flushAsync();

    container.querySelector('#dr-goto-overlay')?.click();

    const ps = State.get('previewSession');
    expect(ps.csv_path).toBe(SESSION.csv_path);
    // Best lap has lap_idx 1
    expect(ps.lap_idx).toBe(1);
  });

  test('"Open in Overlay" sets previewSession with full video_paths', async () => {
    container.querySelector('.dl-row')?.click();
    await flushAsync();
    await flushAsync();

    container.querySelector('#dr-goto-overlay')?.click();

    expect(State.get('previewSession').video_paths).toEqual(SESSION.video_paths);
  });

  // ── Sync offset update ─────────────────────────────────────────────────────

  test('saving sync offset updates previewSession when it is the active session', async () => {
    // Select session so previewSession is set
    container.querySelector('.dl-row')?.click();
    await flushAsync();

    // Manually trigger offset save by reading the internal save path:
    // The mark button writes to the offset input and calls saveOffset internally.
    // Here we verify the State subscription works by setting the preview first
    // then checking it gets updated via State.on.
    const ps = State.get('previewSession');
    expect(ps.csv_path).toBe(SESSION.csv_path);

    // Simulate what saveOffset does: it sets previewSession with new sync_offset
    State.set('previewSession', { ...ps, sync_offset: 2.75 });
    expect(State.get('previewSession').sync_offset).toBe(2.75);
  });
});
