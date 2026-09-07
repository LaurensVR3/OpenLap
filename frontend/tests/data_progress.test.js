/**
 * Data page — auto-sync progress messages.
 *
 * The reported symptom was "Auto-syncing session 0 of 0 - 809s of video
 * decoded, confidence 4.06x (need 6x)". Three separate faults in one line:
 *
 *   - the position was latched from the first 'processing' event into
 *     page-local state, which is recreated whenever the Data page is opened,
 *     so reopening it mid-run showed "0 of 0";
 *   - two sessions sync concurrently and shared that one counter, so the
 *     number could not say whose confidence was being reported;
 *   - "(need 6x)" was a hardcoded literal, and wrong: 6x only stops the
 *     search early, while anything from 3x up is still accepted once the
 *     whole video has been decoded.
 */
import {
  loadState, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

const A = {
  csv_path: '/data/a.csv', video_paths: ['/v/a.mp4'], matched: true,
  source: 'RaceBox', csv_start: '2026-08-20T08:48:00Z',
  sync_offset: null, is_bike: false, needs_conversion: false,
};
const B = { ...A, csv_path: '/data/b.csv', video_paths: ['/v/b.mp4'],
            csv_start: '2026-08-20T09:37:00Z' };

const CHECKING = {
  status: 'checking', csv_path: A.csv_path,
  current: 2, total: 6, vid_t: 809, confidence: 4.06,
  early_exit_confidence: 6.0, min_confidence: 3.0,
};

describe('Data page — auto-sync progress', () => {
  let container, page, handlers, api;

  beforeEach(async () => {
    loadState();
    handlers = {};
    const router = makeRouter();
    globalThis.Router = router;
    api = makeAPI({
      on: vi.fn((evt, fn) => { handlers[evt] = fn; return () => {}; }),
      getConfig: vi.fn(async () => ({
        all_telemetry_paths: ['/data'], offsets: {}, bike_overrides: {},
        overlay: { is_bike: false, theme: 'Dark', gauges: [] },
        auto_sync_enabled: false,
      })),
      scanSessions:    vi.fn(async () => [A, B]),
      scanAllSessions: vi.fn(async () => [A, B]),
      getSessionMeta:  vi.fn(async () => ({ track: 'Genk', laps: '9', best: '', best_secs: null })),
    });
    globalThis.API = api;

    loadPage('pages/data.js');
    container = makeContainer();
    page = router.getPage('data');
    await page.mount(container);
    await flushAsync();
    await flushAsync();
  });

  afterEach(() => {
    page?.unmount();
    cleanupContainer(container);
    vi.restoreAllMocks();
  });

  const status = () => container.querySelector('#scan-status').textContent;

  test('a checking event alone still reports its position', () => {
    // No preceding 'processing' — exactly the state after reopening the page
    // while a run is already going, which used to render "0 of 0".
    handlers['auto_sync_progress'](CHECKING);
    expect(status()).toContain('2 of 6');
    expect(status()).not.toContain('0 of 0');
  });

  test('the session is named, not just counted', () => {
    handlers['auto_sync_progress'](CHECKING);
    expect(status()).toContain('Genk');
  });

  test('two concurrent sessions each report their own position', () => {
    handlers['auto_sync_progress'](CHECKING);
    expect(status()).toContain('2 of 6');
    handlers['auto_sync_progress']({ ...CHECKING, csv_path: B.csv_path, current: 3 });
    expect(status()).toContain('3 of 6');
  });

  test('both thresholds are explained, using the values the backend sent', () => {
    handlers['auto_sync_progress'](CHECKING);
    const text = status();
    expect(text).toContain('6× stops early');
    expect(text).toContain('3× accepted');
    // The old wording read as a pass mark the session was failing to reach.
    expect(text).not.toContain('need 6');
  });

  test('the decoded duration and confidence are still shown', () => {
    handlers['auto_sync_progress'](CHECKING);
    expect(status()).toContain('809s decoded');
    expect(status()).toContain('4.06×');
  });

  test('thresholds are omitted rather than invented when absent', () => {
    const { early_exit_confidence, min_confidence, ...withoutGates } = CHECKING;
    handlers['auto_sync_progress'](withoutGates);
    expect(status()).toContain('4.06×');
    expect(status()).not.toContain('undefined');
  });

  test('a processing event names the session too', () => {
    handlers['auto_sync_progress']({
      status: 'processing', csv_path: A.csv_path, current: 1, total: 6,
    });
    expect(status()).toContain('1 of 6');
    expect(status()).toContain('Genk');
  });
});
