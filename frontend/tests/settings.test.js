/**
 * Settings page — mount/unmount race tests.
 *
 * Regression coverage: mount() awaits API.getConfig() before writing to the
 * container. If the user navigates away (unmount() runs) before that await
 * resolves, the stale mount() must not overwrite the container with Settings'
 * HTML once getConfig() finally resolves — otherwise the shared #view element
 * ends up showing Settings' markup while the router thinks it's on another
 * page (see data.js's `_container` guard pattern, mirrored here).
 */
import {
  loadState, loadPage, makeRouter, makeAPI,
  makeContainer, cleanupContainer, flushAsync,
} from './helpers.js';

describe('Settings page — mount/unmount race', () => {
  let router, container, page;

  beforeEach(() => {
    loadState();
    router = makeRouter();
    globalThis.Router = router;
  });

  afterEach(() => {
    cleanupContainer(container);
  });

  test('does not overwrite the container if unmounted before getConfig() resolves', async () => {
    let resolveConfig;
    const configPromise = new Promise(r => { resolveConfig = r; });

    globalThis.API = makeAPI({ getConfig: vi.fn(() => configPromise) });
    loadPage('pages/settings.js');
    container = makeContainer();
    page      = router.getPage('settings');

    container.innerHTML = '<div class="marker">some other page</div>';
    const mountPromise = page.mount(container);

    // Navigate away before getConfig() resolves
    page.unmount();

    // Now let getConfig() resolve
    resolveConfig({ racebox_path: '', aim_path: '', motec_path: '', gpx_path: '',
                     vbox_path: '', video_path: '', export_path: '' });
    await mountPromise;
    await flushAsync();

    // The container must still show whatever the OTHER page rendered —
    // Settings' suspended mount() must not have clobbered it.
    expect(container.querySelector('.marker')).not.toBeNull();
    expect(container.querySelector('.settings-page')).toBeNull();
  });

  test('renders normally when not interrupted', async () => {
    globalThis.API = makeAPI({
      getConfig: vi.fn(async () => ({
        racebox_path: '', aim_path: '', motec_path: '', gpx_path: '', vbox_path: '',
        video_path: '', export_path: '',
      })),
      raceboxPlaywrightStatus: vi.fn(async () => ({ playwright: false, chromium: false })),
      aimDllStatus:            vi.fn(async () => ({ found: false, path: '' })),
      getAboutInfo:            vi.fn(async () => ({ version: '0.0.0', python: '3.x', config: '' })),
    });
    loadPage('pages/settings.js');
    container = makeContainer();
    page      = router.getPage('settings');

    await page.mount(container);
    await flushAsync();

    expect(container.querySelector('.settings-page')).not.toBeNull();
    page.unmount();
  });
});
