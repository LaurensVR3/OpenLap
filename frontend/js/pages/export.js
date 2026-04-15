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
  let _progressPct = 0;
  let _progressMsg = '';

  // ── Mount / Unmount ──────────────────────────────────────────────────────────

  function _itemFromPreview(ps) {
    if (!ps) return null;
    return {
      csv_path:    ps.csv_path,
      lap_idx:     ps.lap_idx,
      video_paths: ps.video_paths || [],
      sync_offset: ps.sync_offset ?? 0,
      source:      ps.source || '',
      duration:    null,
      is_best:     false,
    };
  }

  async function mount(container) {
    _container = container;
    // Do NOT reset _logLines or _exporting — they persist while navigating away

    // Always (re-)derive selectedItems from previewSession on mount
    const initPs = State.get('previewSession');
    State.set('selectedItems', initPs ? [_itemFromPreview(initPs)] : []);

    const cfg = State.get('config') || {};

    const items = State.get('selectedItems') || [];

    container.innerHTML = _buildHTML(cfg, items);

    _bindEvents(cfg, items);
    _refreshItemList(items);
    _updateStartBtn(items);

    // Restore persistent log and export state across navigation
    const logEl = container.querySelector('#exp-log');
    if (logEl && _logLines.length) {
      logEl.value = _logLines.join('\n');
      logEl.scrollTop = logEl.scrollHeight;
    }
    if (_exporting) {
      _setExporting(true);
      _setBadge('Running', 'badge-run');
    }
    // Restore progress bar position
    if (_progressPct > 0 || _progressMsg) {
      _setProgress(_progressPct, _progressMsg);
    }

    // Re-render if items change while on this page
    _unlistenFns.push(State.on('selectedItems', newItems => {
      _refreshItemList(newItems);
      _updateStartBtn(newItems);
    }));

    // Keep selectedItems in sync when previewSession changes while on this page
    _unlistenFns.push(State.on('previewSession', ps => {
      if (ps) State.set('selectedItems', [_itemFromPreview(ps)]);
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

      <!-- Timing -->
      <div class="card export-card">
        <div class="card-header"><span class="card-title">Timing</span></div>
        <div class="card-body">
          <div class="form-row">
            <label>Padding (s)</label>
            <input type="number" id="exp-padding" class="input-field input-narrow" value="5" min="0" max="60" step="0.5">
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
            <label>Scope</label>
            <select id="exp-scope" class="input-field">
              <option value="selected_lap">Selected Lap</option>
              <option value="fastest_lap">Fastest Lap</option>
              <option value="all_laps">All Laps</option>
              <option value="full">Full Session</option>
            </select>
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

    // Show clip rows only when scope=clip
    const _syncClipRows = () => {
      const scope = $('exp-scope')?.value || 'selected_lap';
      _container.querySelectorAll('.exp-clip-row').forEach(r => {
        r.classList.toggle('hidden', scope !== 'clip');
      });
    };
    _syncClipRows();
    $('exp-scope').addEventListener('change', _syncClipRows);

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
      // Use track name if available, fall back to CSV basename
      const track    = sess.laps[0]?.track || '';
      const csvDate  = sess.laps[0]?.csv_start
        ? new Date(sess.laps[0].csv_start).toLocaleDateString() : '';
      const headline = track
        ? `${track}${csvDate ? '  ·  ' + csvDate : ''}`
        : _baseName(sess.csv_path);

      const chips = sess.laps.map(l => {
        const label = l.lap_label
          ? `Lap ${l.lap_idx + 1}${l.is_best ? ' ★' : ''} — ${_fmtTime(l.duration)}`
          : (l.duration != null ? _fmtTime(l.duration) : '—');
        const scope = l.scope ? ` [${l.scope.replace('_', ' ')}]` : '';
        return `<span class="lap-chip${l.is_best ? ' best' : ''}"
                      title="${_esc(l.csv_path)} · lap ${l.lap_idx + 1}${_esc(scope)}">
                  ${_esc(label)}${_esc(scope)}
                  <button class="chip-remove" data-csv="${_esc(l.csv_path)}" data-lapidx="${l.lap_idx}" title="Remove this lap">✕</button>
                </span>`;
      }).join('');
      return `
        <div class="item-row">
          <div class="item-name" title="${_esc(sess.csv_path)}">${_esc(headline)}</div>
          <div class="item-chips">${chips}</div>
        </div>`;
    }).join('');

    // Per-lap remove buttons (inside each chip)
    list.querySelectorAll('.chip-remove').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const csv    = btn.dataset.csv;
        const lapIdx = parseInt(btn.dataset.lapidx);
        const current = State.get('selectedItems') || [];
        State.set('selectedItems', current.filter(i => !(i.csv_path === csv && i.lap_idx === lapIdx)));
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

    // Fetch config (encoder, export path) and overlay layout (ref_mode, is_bike, show_map/tel)
    const [cfg, layout] = await Promise.all([
      API.getConfig().catch(() => ({})),
      API.getOverlay().catch(() => ({})),
    ]);

    const params = {
      items:        items,
      scope:        $('exp-scope')?.value    || 'selected_lap',
      clip_start_s: parseFloat($('exp-clip-start')?.value) || 0,
      clip_end_s:   parseFloat($('exp-clip-end')?.value)   || 0,
      padding:      parseFloat($('exp-padding').value)    || 5.0,
      ref_mode:     layout.ref_mode   || 'none',
      encoder:      cfg.encoder       || 'libx264',
      crf:          cfg.crf           ?? 18,
      workers:      cfg.workers       ?? 4,
      is_bike:      layout.is_bike    ?? false,
      show_map:     layout.show_map   ?? true,
      show_tel:     layout.show_tel   ?? true,
      export_path:  cfg.export_path   || '',
      layout,
    };

    _logLines    = [];
    _progressPct = 0;
    _progressMsg = '';
    $('exp-log').value = '';
    _setProgress(0, 'Starting…');
    _setExporting(true);
    _setBadge('Running', 'badge-run');

    await API.startExport(params);
  }

  // ── Event handlers ────────────────────────────────────────────────────────────

  function _onProgress(detail) {
    // Python progress_cb sends 0–100 directly; clamp to avoid display glitches
    const pct = Math.min(100, Math.max(0, Math.round(detail.value || 0)));
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
    if (start) {
      start.disabled    = active || items.length === 0;
      start.textContent = active ? 'Exporting…' : 'Start Export';
      start.classList.toggle('btn-exporting', active);
    }
    if (cancel) { cancel.classList.toggle('hidden', !active); }
  }

  function _setProgress(pct, msg) {
    _progressPct = pct;
    _progressMsg = msg;
    if (!_container) return;
    const fill = _container.querySelector('#exp-progress-fill');
    const pctEl = _container.querySelector('#exp-progress-pct');
    const msgEl = _container.querySelector('#exp-progress-msg');
    if (fill)  fill.style.width = pct + '%';
    if (pctEl) pctEl.textContent = Math.round(pct) + '%';
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
