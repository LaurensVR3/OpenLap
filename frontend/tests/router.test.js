/**
 * router.js — hash-based SPA router tests.
 *
 * Covers basic register/navigate/unmount behaviour, plus a regression test
 * for the "Page not found" view: `name` comes straight from
 * window.location.hash and was interpolated unescaped into innerHTML.
 */
import { loadRouter, flushAsync } from './helpers.js';

describe('Router', () => {
  let Router, view;

  beforeEach(() => {
    document.body.innerHTML = '<div id="view"></div><nav></nav>';
    view = document.getElementById('view');
    Router = loadRouter();
  });

  test('register() then navigate() mounts the registered page into #view', async () => {
    const mount = vi.fn(async (el) => { el.innerHTML = '<p>hello</p>'; });
    Router.register('home', { mount });

    await Router.navigate('home');

    expect(mount).toHaveBeenCalledWith(view);
    expect(view.innerHTML).toContain('hello');
  });

  test('navigating away unmounts the previous page', async () => {
    const unmountA = vi.fn();
    Router.register('a', { mount: async () => {}, unmount: unmountA });
    Router.register('b', { mount: async () => {} });

    await Router.navigate('a');
    await Router.navigate('b');

    expect(unmountA).toHaveBeenCalledOnce();
  });

  test('updates the location hash to the navigated page', async () => {
    Router.register('settings', { mount: async () => {} });
    await Router.navigate('settings');
    expect(window.location.hash).toBe('#settings');
  });

  test('unknown route renders a "Page not found" view without crashing', async () => {
    await Router.navigate('nonexistent-page');
    expect(view.textContent).toContain('Page not found');
    expect(view.textContent).toContain('nonexistent-page');
  });

  test('unknown route with HTML-special characters in the name is escaped, not injected', async () => {
    const malicious = '<img src=x onerror="window.__pwned=true">';
    await Router.navigate(malicious);
    await flushAsync();

    // Must render as inert text, never as a live element the browser executes.
    expect(view.querySelector('img')).toBeNull();
    expect(window.__pwned).toBeUndefined();
    expect(view.innerHTML).toContain('&lt;img');
  });
});
