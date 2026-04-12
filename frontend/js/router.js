/**
 * router.js — Hash-based client-side router.
 * Pages register themselves via Router.register(name, { mount(el), unmount() }).
 */
const Router = (() => {
  const _pages = {};
  let _current = null;

  function register(name, page) {
    _pages[name] = page;
  }

  async function navigate(name) {
    const view = document.getElementById('view');
    if (!view) return;

    // Unmount current
    if (_current && _pages[_current] && _pages[_current].unmount) {
      _pages[_current].unmount();
    }

    // Update nav highlight
    document.querySelectorAll('.nav-item').forEach(el => {
      el.classList.toggle('active', el.dataset.page === name);
    });

    // Clear and mount new page
    view.innerHTML = '';
    _current = name;

    const page = _pages[name];
    if (page) {
      await page.mount(view);
    } else {
      view.innerHTML = `<div class="empty-state">
        <div class="empty-icon">🚫</div>
        <div class="empty-title">Page not found</div>
        <div>${name}</div>
      </div>`;
    }

    window.location.hash = name;
  }

  function init() {
    // Wire up nav clicks
    document.querySelectorAll('.nav-item').forEach(el => {
      el.addEventListener('click', () => navigate(el.dataset.page));
    });

    // Load initial page from hash or default to 'data'
    const hash = window.location.hash.replace('#', '') || 'data';
    navigate(hash);
  }

  return { register, navigate, init };
})();
