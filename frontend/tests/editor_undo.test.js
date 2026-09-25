/**
 * Overlay editor — undo/redo and keyboard shortcuts.
 */
import {
  loadState, loadExportParams, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

describe('Overlay editor — undo and shortcuts', () => {
  let container, page, api;
  const key = (k, opts = {}) => document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...opts }));
  const saved = () => api.saveOverlay.mock.calls.at(-1)?.[0];

  beforeEach(async () => {
    loadState();
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
      get: (t, p) => (p === 'measureText' ? () => ({ width: 10 })
                    : p === 'createLinearGradient' ? () => ({ addColorStop() {} })
                    : () => {}),
      set: () => true,
    });
    const router = makeRouter();
    globalThis.Router = router;
    const layout = { is_bike: false, theme: 'Dark', gauges: [
      { type: 'Numeric', channel: 'speed', visible: true, x: 0.1, y: 0.1, w: 0.2, h: 0.2 },
    ] };
    api = makeAPI({
      getOverlay: vi.fn(async () => JSON.parse(JSON.stringify(layout))),
      getConfig:  vi.fn(async () => ({ overlay: layout, presets: {} })),
    });
    globalThis.API = api;
    loadExportParams();
    loadPage('pages/editor.js');
    container = makeContainer();
    page = router.getPage('editor');
    await page.mount(container);
    await flushAsync();
    container.querySelector('.gauge-list-item').click();   // select the gauge
    await flushAsync();
  });

  afterEach(() => { page?.unmount(); cleanupContainer(container); });

  test('Delete removes the selected gauge, Ctrl+Z brings it back, Ctrl+Y removes it again', async () => {
    key('Delete');
    expect(saved().gauges).toHaveLength(0);
    key('z', { ctrlKey: true });
    expect(saved().gauges).toHaveLength(1);
    key('y', { ctrlKey: true });
    expect(saved().gauges).toHaveLength(0);
  });

  test('arrow keys nudge the gauge, and a burst of nudges undoes in one step', async () => {
    container.querySelector('.gauge-list-item').click();
    key('ArrowRight'); key('ArrowRight'); key('ArrowRight');
    expect(saved().gauges[0].x).toBeCloseTo(0.115, 5);
    key('z', { ctrlKey: true });
    expect(saved().gauges[0].x).toBeCloseTo(0.1, 5);
  });

  test('Ctrl+D duplicates the selected gauge', async () => {
    key('d', { ctrlKey: true });
    expect(saved().gauges).toHaveLength(2);
    expect(saved().gauges[1].type).toBe('Numeric');
  });

  test('shortcuts are ignored while typing in a field', async () => {
    const before = api.saveOverlay.mock.calls.length;
    const input = document.createElement('input');
    container.appendChild(input);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
    expect(api.saveOverlay.mock.calls.length).toBe(before);
  });
});
