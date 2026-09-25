/**
 * Overlay editor — text from telemetry files is never parsed as HTML.
 *
 * Channel names come straight out of the files a user loads (MoTeC/AIM/VBOX
 * headers), and the editor runs with window.pywebview.api available, so a
 * crafted channel name rendered as markup would be script with access to
 * the whole app. The Multi-Line channel picker used to interpolate them raw.
 */
import {
  loadState, loadExportParams, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

const EVIL = '</select><img src=x onerror="window.__pwned=1">"><b>';

describe('Overlay editor — escaping', () => {
  let container, page;

  beforeEach(async () => {
    loadState();
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    const fakeCtx = {
      clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
      stroke() {}, fill() {}, save() {}, restore() {}, arc() {}, closePath() {},
      measureText: () => ({ width: 10 }), roundRect() {}, fillText() {}, strokeText() {},
      setLineDash() {}, translate() {}, rotate() {}, scale() {}, quadraticCurveTo() {},
      bezierCurveTo() {}, rect() {}, clip() {}, createLinearGradient: () => ({ addColorStop() {} }),
    };
    HTMLCanvasElement.prototype.getContext = () => fakeCtx;
    const router = makeRouter();
    globalThis.Router = router;
    const layout = { is_bike: false, theme: 'Dark', gauges: [
      { type: 'Multi-Line', multi_channels: [EVIL], visible: true, x: 0.1, y: 0.1, w: 0.3, h: 0.2 },
    ] };
    globalThis.API = makeAPI({
      getVideoServerPort:  vi.fn(async () => 0),
      getConfig:           vi.fn(async () => ({ overlay: layout, presets: {} })),
      getOverlay:          vi.fn(async () => layout),
      listPresets:         vi.fn(async () => [EVIL]),
      getSessionMeta:      vi.fn(async () => ({ track: 'T' })),
      getLaps:             vi.fn(async () => [{ lap_idx: 0, lap_num: 1, duration: 60, elapsed_start: 0 }]),
      loadLapHistory:      vi.fn(async () => []),
      getAvailableChannels: vi.fn(async () => [{ key: EVIL, label: EVIL, unit: '', noisy: false }]),
      getTrackMapGeometry: vi.fn(async () => ({ lats: [], lons: [] })),
    });
    loadExportParams();
    loadPage('pages/editor.js');
    container = makeContainer();
    page = router.getPage('editor');
    State.set('previewSession', { csv_path: '/s.csv', video_paths: [], sync_offset: 0, lap_idx: 0 });
    await page.mount(container);
    await flushAsync();
    await flushAsync();
    container.querySelector('.gauge-list-item')?.click();
    await flushAsync();
  });

  afterEach(() => {
    page?.unmount();
    cleanupContainer(container);
    delete window.__pwned;
  });

  test('a crafted channel name is shown as text, not parsed as markup', () => {
    expect(container.querySelector('img')).toBeNull();
    expect(window.__pwned).toBeUndefined();
    const texts = [...container.querySelectorAll('option, span')].map(e => e.textContent);
    expect(texts).toContain(EVIL);
  });
});
