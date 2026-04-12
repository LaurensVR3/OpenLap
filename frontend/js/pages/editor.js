/**
 * editor.js — Overlay Editor page (Phase 2).
 * Stub implementation for Phase 1.
 */
(function () {
  async function mount(container) {
    container.innerHTML = `
      <div class="page">
        <div class="toolbar">
          <div class="toolbar-left">
            <span class="page-title">Overlay Editor</span>
          </div>
        </div>
        <div class="page-divider"></div>
        <div class="empty-state">
          <div class="empty-icon">🎨</div>
          <div class="empty-title">Coming in Phase 2</div>
          <div>Drag-and-drop overlay editor with live Canvas gauge previews.</div>
        </div>
      </div>`;
  }

  function unmount() {}

  Router.register('editor', { mount, unmount });
})();
