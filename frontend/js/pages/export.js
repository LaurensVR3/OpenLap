/**
 * export.js — Export page (Phase 3).
 * Stub implementation for Phase 1.
 */
(function () {
  async function mount(container) {
    const items = State.get('selectedItems') || [];
    container.innerHTML = `
      <div class="page">
        <div class="toolbar">
          <div class="toolbar-left">
            <span class="page-title">Export</span>
          </div>
        </div>
        <div class="page-divider"></div>
        <div class="empty-state">
          <div class="empty-icon">🎬</div>
          <div class="empty-title">Coming in Phase 3</div>
          <div>
            ${items.length > 0
              ? `${items.length} lap${items.length !== 1 ? 's' : ''} queued for export.`
              : 'Select laps on the Data page first.'}
          </div>
        </div>
      </div>`;
  }

  function unmount() {}

  Router.register('export', { mount, unmount });
})();
