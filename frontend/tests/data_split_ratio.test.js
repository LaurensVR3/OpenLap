/**
 * Data page — the split-ratio preference degrades when localStorage is absent.
 *
 * The ratio is a cosmetic convenience, but it is read during mount(), so a bare
 * `localStorage` reference takes the whole page down with it wherever the API is
 * missing (jsdom) or refused (WKWebView on a file:// origin throws SecurityError
 * on property *access*, not just on the call).
 */
import {
  loadState, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

const KEY = 'data-split-ratio';

/** Install a stand-in at globalThis.localStorage; returns a restore fn. */
function withStorage(impl) {
  const had  = Object.prototype.hasOwnProperty.call(globalThis, 'localStorage');
  const prev = had ? Object.getOwnPropertyDescriptor(globalThis, 'localStorage') : null;
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true, writable: true, value: impl,
  });
  return () => {
    if (prev) Object.defineProperty(globalThis, 'localStorage', prev);
    else delete globalThis.localStorage;
  };
}

/** A working localStorage backed by a plain object. */
function fakeStorage(seed = {}) {
  const store = { ...seed };
  return {
    getItem: vi.fn(k => (k in store ? store[k] : null)),
    setItem: vi.fn((k, v) => { store[k] = String(v); }),
    _store: store,
  };
}

async function mountPage() {
  loadState();
  const router = makeRouter();
  globalThis.Router = router;
  globalThis.API = makeAPI();

  loadPage('pages/data.js');
  const container = makeContainer();
  const page = router.getPage('data');
  await page.mount(container);
  await flushAsync();
  return { container, page };
}

describe('Data page — split ratio persistence', () => {
  let ctx, restore;

  afterEach(() => {
    ctx?.page?.unmount();
    cleanupContainer(ctx?.container);
    restore?.();
    restore = undefined;
    vi.restoreAllMocks();
  });

  it('mounts and uses the 50% default when localStorage is undefined', async () => {
    restore = withStorage(undefined);
    ctx = await mountPage();

    const left = ctx.container.querySelector('#data-left-panel');
    expect(left).toBeTruthy();
    expect(left.style.flexBasis).toBe('50%');
  });

  it('mounts and uses the default when localStorage access throws', async () => {
    // WKWebView on an opaque origin: touching the property is itself a throw.
    const had  = Object.prototype.hasOwnProperty.call(globalThis, 'localStorage');
    const prev = had ? Object.getOwnPropertyDescriptor(globalThis, 'localStorage') : null;
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() { throw new DOMException('The operation is insecure.', 'SecurityError'); },
    });
    restore = () => {
      if (prev) Object.defineProperty(globalThis, 'localStorage', prev);
      else delete globalThis.localStorage;
    };

    ctx = await mountPage();
    expect(ctx.container.querySelector('#data-left-panel').style.flexBasis).toBe('50%');
  });

  it('restores a previously saved ratio when localStorage works', async () => {
    const ls = fakeStorage({ [KEY]: '0.3' });
    restore = withStorage(ls);
    ctx = await mountPage();

    expect(ls.getItem).toHaveBeenCalledWith(KEY);
    expect(ctx.container.querySelector('#data-left-panel').style.flexBasis).toBe('30%');
  });

  it('does not throw on drag-end when localStorage writes are refused', async () => {
    restore = withStorage({
      getItem: () => null,
      setItem: () => { throw new DOMException('QuotaExceededError'); },
    });
    ctx = await mountPage();

    const split  = ctx.container.querySelector('#data-split');
    const left   = ctx.container.querySelector('#data-left-panel');
    const resize = ctx.container.querySelector('#data-resizer');
    expect(resize).toBeTruthy();

    // jsdom has no layout, so give the drag real geometry to divide.
    // (its CSSOM also normalises '40.00%' to '40%' on the way back out)
    split.getBoundingClientRect = () => ({ left: 0, width: 1000 });
    left.getBoundingClientRect  = () => ({ left: 0, width: 400  });

    // A throw inside a listener does not propagate out of dispatchEvent — jsdom
    // reports it as a window 'error' event instead, so assert on that.
    const onError = vi.fn();
    window.addEventListener('error', onError);

    resize.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 400 }));
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    window.removeEventListener('error', onError);

    expect(onError).not.toHaveBeenCalled();

    expect(left.style.flexBasis).toBe('40%');
  });
});
