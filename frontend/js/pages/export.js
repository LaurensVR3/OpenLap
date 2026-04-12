/**
 * export.js — Export page (Phase 3).
 *
 * Reads selected items from State.get('selectedItems').
 * Calls API.startExport(params) / API.cancelExport().
 * Receives progress via openlap CustomEvents: export_progress, export_log, export_done.
 */
(function () {
  let _container   = null;
  let _exporting   = false;
  let _logLines    = [];
  let _unlistenFns = [];

  // ── Mount / Unmount ──────────────────────────────────────────────────────────

  async function mount(container) {
    _container = container;
    _logLines  = [];
    _exporting = false;

    const cfg   = State.get('config') || {};
    const items = State.get('selectedItems') || [];

    container.innerHTML = _buildHTML(cfg, items);

    _bindEvents(cfg, items);
    _refreshItemList(items);
    _updateStartBtn(items);

    // Re-render if items change while on this page
    _unlistenFns.push(State.on('selectedItems', newItems => {
      _refreshItemList(newItems);
      _updateStartBtn(newItems);
    }));

    // Listen for push events from Python
    _unlistenFns.push(API.on('export_progress', _onProgress));
    _unlistenFns.push(API.on('export_log',      _onLog));
    _unlistenFns.push(API.on('export_done',      _onDone));
  }

  function unmount() {
    _unlistenFns.forEach(fn => fn());
    _unlistenFns = [];
    _container   = null;
  }

  // ── HTML skeleton ─────────────────────────────────────────────────────────────

  function _buildHTML(cfg, items) {
    const exportPath = cfg.export_path || '';
    return `
<div class="page export-page">
  <div class="toolbar">
    <div class="toolbar-left">
      <span class="page-title">Export</span>
    </div>
    <div class="toolbar-right">
      <button class="btn btn-primary" id="exp-start-btn" disabled>Start Export</button>
      <button class="btn btn-secondary hidden" id="exp-cancel-btn">Cancel</button>
    </div>
  </div>
  <div class="page-divider"></div>

  <div class="export-layout">

    <!-- LEFT: config panels -->
    <div class="export-config">

      <!-- Selected items -->
      <div class="card export-card">
        <div class="card-header">
          <span class="card-title">Queued Laps</span>
          <span class="badge" id="exp-item-count">0</span>
        </div>
        <div class="card-body" id="exp-item-list">
          <div class="empty-hint">No laps selected — go to the Data page to add laps.</div>
        </div>
      </div>

      <!-- Scope & timing -->
      <div class="card export-card">
        <div class="card-header"><span class="card-title">Scope &amp; Timing</span></div>
        <div class="card-body">
          <div class="form-row">
            <label>Scope</label>
            <select id="exp-scope" class="input-field">
              <option value="fastest">Fastest lap per session</option>
              <option value="all">All selected laps</option>
              <option value="clip">Clip (manual start/end)</option>
            </select>
          </div>
          <div class="form-row exp-clip-row hidden">
            <label>Clip start (s)</label>
            <input type="number" id="exp-clip-start" class="input-field input-narrow" value="0" min="0" step="0.1">
          </div>
          <div class="form-row exp-clip-row hidden">
            <label>Clip end (s)</label>
            <input type="number" id="exp-clip-end" class="input-field input-narrow" value="0" min="0" step="0.1">
          </div>
          <div class="form-row">
            <label>Padding (s)</label>
            <input type="number" id="exp-padding" class="input-field input-narrow" value="5" min="0" max="60" step="0.5">
          </div>
        </div>
      </div>

      <!-- Reference lap -->
      <div class="card export-card">
        <div class="card-header"><span class="card-title">Reference Lap</span></div>
        <div class="card-body">
          <div class="form-row">
            <label>Mode</label>
            <select id="exp-ref-mode" class="input-field">
              <option value="none">None</option>
              <option value="best_in_session">Best in session</option>
              <option value="best_overall">Best overall</option>
            </select>
          </div>
        </div>
      </div>

      <!-- Encoder settings -->
      <div class="card export-card">
        <div class="card-header"><span class="card-title">Encoder</span></div>
        <div class="card-body">
          <div class="form-row">
            <label>Codec</label>
            <select id="exp-encoder" class="input-field">
              <option value="libx264">H.264 (libx264) — Universal</option>
              <option value="libx265">H.265 (libx265) — Smaller</option>
              <option value="h264_nvenc">H.264 NVENC — NVIDIA GPU</option>
              <option value="h264_videotoolbox">H.264 VideoToolbox — Apple</option>
            </select>
          </div>
          <div class="form-row">
            <label>Quality (CRF)</label>
            <div class="range-row">
              <input type="range" id="exp-crf" min="12" max="32" value="18" step="1">
              <span class="range-val" id="exp-crf-val">18</span>
            </div>
          </div>
          <div class="form-row">
            <label>Workers</label>
            <input type="number" id="exp-workers" class="input-field input-narrow" value="4" min="1" max="16" step="1">
          </div>
        </div>
      </div>

      <!-- Overlay options -->
      <div class="card export-card">
        <div class="card-header"><span class="card-title">Overlay Options</span></div>
        <div class="card-body">
          <div class="form-row">
            <label>Bike mode</label>
            <label class="toggle">
              <input type="checkbox" id="exp-bike">
              <span class="toggle-slider"></span>
            </label>
          </div>
          <div class="form-row">
            <label>Show map</label>
            <label class="toggle">
              <input type="checkbox" id="exp-show-map" checked>
              <span class="toggle-slider"></span>
            </label>
          </div>
          <div class="form-row">
            <label>Show telemetry</label>
            <label class="toggle">
              <input type="checkbox" id="exp-show-tel" checked>
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>
      </div>

      <!-- Output -->
      <div class="card export-card">
        <div class="card-header"><span class="card-title">Output</span></div>
        <div class="card-body">
          <div class="form-row">
            <label>Folder</label>
            <div class="path-row">
              <input type="text" id="exp-output-path" class="input-field" placeholder="Same as video source" value="${_esc(exportPath)}">
              <button class="btn btn-secondary" id="exp-browse-output">Browse</button>
            </div>
          </div>
        </div>
      </div>

    </div><!-- /.export-config -->

    <!-- RIGHT: progress + log -->
    <div class="export-progress-panel">
      <div class="card export-card full-height">
        <div class="card-header">
          <span class="card-title">Progress</span>
          <span class="badge badge-dim" id="exp-status-badge">Idle</span>
        </div>
        <div class="card-body progress-body">
          <div class="progress-bar-wrap">
            <div class="progress-bar-track">
              <div class="progress-bar-fill" id="exp-progress-fill" style="width:0%"></div>
            </div>
            <span class="progress-pct" id="exp-progress-pct">0%</span>
          </div>
          <div class="progress-status" id="exp-progress-msg"></div>
          <textarea class="log-area" id="exp-log" readonly placeholder="Export log will appear here…"></textarea>
        </div>
      </div>
    </div>

  </div><!-- /.export-layout -->
</div>`;
  }

  // ── Event wiring ──────────────────────────────────────────────────────────────

  function _bindEvents(cfg, items) {
    const $ = id => _container.querySelector('#' + id);

    // Scope toggle shows/hides clip rows
    $('exp-scope').addEventListener('change', e => {
      const isClip = e.target.value === 'clip';
      _container.querySelectorAll('.exp-clip-row').forEach(r => {
        r.classList.toggle('hidden', !isClip);
      });
    });

    // CRF slider label sync
    $('exp-crf').addEventListener('input', e => {
      $('exp-crf-val').textContent = e.target.value;
    });

    // Browse output folder
    $('exp-browse-output').addEventListener('click', async () => {
      const path = await API.openFolderDialog();
      if (path) $('exp-output-path').value = path;
    });

    // Start
    $('exp-start-btn').addEventListener('click', () => _startExport());

    // Cancel
    $('exp-cancel-btn').addEventListener('click', async () => {
      await API.cancelExport();
      _setExporting(false);
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────

  function _refreshItemList(items) {
    if (!_container) return;
    const list  = _container.querySelector('#exp-item-list');
    const badge = _container.querySelector('#exp-item-count');
    if (!list) return;

    badge.textContent = items.length;

    if (!items.length) {
      list.innerHTML = '<div class="empty-hint">No laps selected — go to the Data page to add laps.</div>';
      return;
    }

    // Group items by session CSV path for a tidy display
    const bySession = {};
    for (const item of items) {
      const key = item.csv_path || 'Unknown';
      if (!bySession[key]) bySession[key] = { csv_path: key, source: item.source || '', laps: [] };
      bySession[key].laps.push(item);
    }

    list.innerHTML = Object.values(bySession).map(sess => {
      const name = _baseName(sess.csv_path);
      const chips = sess.laps.map(l => {
        const dur = l.duration != null ? _fmtTime(l.duration) : '—';
        return `<span class="lap-chip${l.is_best ? ' best' : ''}" title="${_esc(l.csv_path)} lap ${l.lap_idx}">${dur}</span>`;
      }).join('');
      return `
        <div class="item-row">
          <div class="item-name" title="${_esc(sess.csv_path)}">${_esc(name)}</div>
          <div class="item-chips">${chips}</div>
          <button class="btn-icon item-remove" data-csv="${_esc(sess.csv_path)}" title="Remove">✕</button>
        </div>`;
    }).join('');

    // Remove-all-from-session button
    list.querySelectorAll('.item-remove').forEach(btn => {
      btn.addEventListener('click', () => {
        const csv     = btn.dataset.csv;
        const current = State.get('selectedItems') || [];
        State.set('selectedItems', current.filter(i => i.csv_path !== csv));
      });
    });
  }

  function _updateStartBtn(items) {
    if (!_container) return;
    const btn = _container.querySelector('#exp-start-btn');
    if (btn) btn.disabled = items.length === 0 || _exporting;
  }

  async function _startExport() {
    if (!_container) return;
    const items = State.get('selectedItems') || [];
    if (!items.length) return;

    const $ = id => _container.querySelector('#' + id);

    const params = {
      items:        items,
      scope:        $('exp-scope').value,
      clip_start_s: parseFloat($('exp-clip-start').value) || 0,
      clip_end_s:   parseFloat($('exp-clip-end').value)   || 0,
      padding:      parseFloat($('exp-padding').value)    || 5.0,
      ref_mode:     $('exp-ref-mode').value,
      encoder:      $('exp-encoder').value,
      crf:          parseInt($('exp-crf').value, 10),
      workers:      parseInt($('exp-workers').value, 10),
      is_bike:      $('exp-bike').checked,
      show_map:     $('exp-show-map').checked,
      show_tel:     $('exp-show-tel').checked,
      export_path:  $('exp-output-path').value.trim(),
    };

    _logLines = [];
    $('exp-log').value = '';
    _setProgress(0, 'Starting…');
    _setExporting(true);
    _setBadge('Running', 'badge-run');

    await API.startExport(params);
  }

  // ── Event handlers ────────────────────────────────────────────────────────────

  function _onProgress(detail) {
    const pct = Math.round((detail.value || 0) * 100);
    _setProgress(pct, detail.message || '');
  }

  function _onLog(detail) {
    if (!_container) return;
    const msg = detail.message || '';
    _logLines.push(msg);
    // Keep last 500 lines to avoid textarea bloat
    if (_logLines.length > 500) _logLines.shift();
    const ta = _container.querySelector('#exp-log');
    if (ta) {
      ta.value = _logLines.join('\n');
      ta.scrollTop = ta.scrollHeight;
    }
  }

  function _onDone(detail) {
    const ok  = detail.ok !== false;
    const msg = detail.message || (ok ? 'Export complete.' : 'Export failed.');
    _onLog({ message: msg });
    _setProgress(ok ? 100 : 0, msg);
    _setExporting(false);
    _setBadge(ok ? 'Done' : 'Error', ok ? 'badge-ok' : 'badge-err');
  }

  // ── UI state helpers ──────────────────────────────────────────────────────────

  function _setExporting(active) {
    _exporting = active;
    if (!_container) return;
    const start  = _container.querySelector('#exp-start-btn');
    const cancel = _container.querySelector('#exp-cancel-btn');
    const items  = State.get('selectedItems') || [];
    if (start)  { start.disabled = active || items.length === 0; }
    if (cancel) { cancel.classList.toggle('hidden', !active); }
  }

  function _setProgress(pct, msg) {
    if (!_container) return;
    const fill = _container.querySelector('#exp-progress-fill');
    const pctEl = _container.querySelector('#exp-progress-pct');
    const msgEl = _container.querySelector('#exp-progress-msg');
    if (fill)  fill.style.width = pct + '%';
    if (pctEl) pctEl.textContent = pct + '%';
    if (msgEl) msgEl.textContent = msg;
  }

  function _setBadge(text, cls) {
    if (!_container) return;
    const badge = _container.querySelector('#exp-status-badge');
    if (!badge) return;
    badge.textContent = text;
    badge.className   = 'badge ' + (cls || 'badge-dim');
  }

  // ── Tiny utilities ────────────────────────────────────────────────────────────

  function _esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _baseName(p) {
    return (p || '').replace(/\\/g, '/').split('/').pop() || p;
  }

  function _fmtTime(secs) {
    if (secs == null || isNaN(secs)) return '—';
    const m  = Math.floor(secs / 60);
    const s  = secs % 60;
    const ss = s.toFixed(3).padStart(6, '0');
    return `${m}:${ss}`;
  }

  Router.register('export', { mount, unmount });
})();
