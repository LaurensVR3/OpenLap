/**
 * export.js — Export page.
 *
 * This page is a queue + progress monitor. The "what to export" decision
 * (scope, padding, overlay-only) is made once per session on the Overlay tab
 * (see editor.js's export menu) and travels with each queued item — Export
 * has no configuration controls of its own. Exports can be started two ways:
 * this page's own "Start Export" (runs whatever's already queued, possibly
 * built up across several sessions via the Overlay tab's "+ Add to Queue"),
 * or the Overlay tab's "Export Now" (queues the current session and starts
 * immediately, navigating here to watch it run).
 *
 * Reads queued items from State.get('selectedItems').
 * Receives progress via openlap CustomEvents: export_progress, export_log, export_done.
 */
(function () {
  let _container   = null;
  let _exporting   = false;
  let _logLines    = [];
  let _unlistenFns = [];   // DOM-lifetime listeners only (State subscriptions)
  let _progressPct = 0;
  let _progressMsg = '';
  let _badgeText   = 'Idle';
  let _badgeCls    = 'badge-dim';

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
      scope:       'selected_lap',
    };
  }

  // Tracks the csv_path+lap_idx of the item we last auto-derived from
  // previewSession, so we can tell "the queue is still just that convenience
  // default" apart from "the user has actually queued laps" (via the Overlay's
  // export menu or the Data page's "Queue for Export"). Only the former may
  // be silently replaced when previewSession changes — a manually-built queue
  // must never be clobbered just by looking at a different lap elsewhere.
  let _autoItemKey = null;

  // Canonical per-item key covering every scope (mirrors editor.js's
  // _queueItemKey) — used both to recognise the auto-derived preview item
  // (below) and to tell which queued items belong to a just-finished export
  // batch, so only those get cleared on success (see _onDone).
  function _queueItemKey(item) {
    if (item.scope === 'lap_range') return `${item.csv_path}::lap_range::${item.lap_range_start}-${item.lap_range_end}`;
    if (item.scope === 'selected_lap' || !item.scope) return `${item.csv_path}::selected_lap::${item.lap_idx}`;
    return `${item.csv_path}::${item.scope}`;
  }

  function _syncSelectedFromPreview(ps) {
    const current = State.get('selectedItems') || [];
    const isAutoOnly = current.length === 0 ||
      (current.length === 1 && _autoItemKey !== null && _queueItemKey(current[0]) === _autoItemKey);
    if (!isAutoOnly || !ps) return;
    const item = _itemFromPreview(ps);
    _autoItemKey = _queueItemKey(item);
    State.set('selectedItems', [item]);
  }

  async function mount(container) {
    _container = container;
    // Do NOT reset _logLines or _exporting — they persist while navigating away

    // Give a convenience default (the currently-previewed lap) only if nothing
    // has been manually queued — never overwrite a real queue on arrival.
    _syncSelectedFromPreview(State.get('previewSession'));

    const items = State.get('selectedItems') || [];

    container.innerHTML = _buildHTML(items);

    _bindEvents();
    _refreshItemList(items);

    // Restore persistent state accumulated while the tab was not visible
    const logEl = container.querySelector('#exp-log');
    if (logEl && _logLines.length) {
      logEl.value = _logLines.join('\n');
      logEl.scrollTop = logEl.scrollHeight;
    }
    _setExporting(_exporting);   // re-applies cancel button visibility
    _setProgress(_progressPct, _progressMsg);
    _setBadge(_badgeText, _badgeCls);

    // State subscriptions — torn down on unmount (DOM lifetime only)
    _unlistenFns.push(State.on('selectedItems', newItems => {
      _refreshItemList(newItems);
    }));
    _unlistenFns.push(State.on('previewSession', ps => {
      _syncSelectedFromPreview(ps);
    }));
    // NOTE: export_progress / export_log / export_done are registered once at
    // module level (see bottom of file) and are never unregistered, so events
    // are captured regardless of which tab is active.
  }

  function unmount() {
    _unlistenFns.forEach(fn => fn());
    _unlistenFns = [];
    _container   = null;
  }

  // ── HTML skeleton ─────────────────────────────────────────────────────────────

  function _buildHTML(items) {
    return `
<div class="page export-page">
  <div class="toolbar">
    <div class="toolbar-left">
      <span class="page-title">Export</span>
    </div>
    <div class="toolbar-right">
      <button class="btn btn-secondary hidden" id="exp-clear-btn">Clear Queue</button>
      <button class="btn btn-accent hidden" id="exp-start-btn">▶ Start Export</button>
      <button class="btn btn-secondary hidden" id="exp-cancel-btn">Cancel</button>
    </div>
  </div>
  <div class="page-divider"></div>

  <div class="export-layout">

    <!-- LEFT: queue -->
    <div class="export-config">
      <div class="card export-card">
        <div class="card-header">
          <span class="card-title">Queued Laps</span>
          <span class="badge" id="exp-item-count">0</span>
        </div>
        <div class="card-body" id="exp-item-list">
          <div class="empty-hint">No laps selected — go to the Data page to add laps.</div>
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

  function _bindEvents() {
    const $ = id => _container.querySelector('#' + id);
    $('exp-cancel-btn').addEventListener('click', async () => {
      await API.cancelExport();
      _setExporting(false);
    });
    $('exp-start-btn').addEventListener('click', _startExport);
    $('exp-clear-btn').addEventListener('click', async () => {
      const ok = await API.confirmClearQueue();
      if (ok) State.set('selectedItems', []);
    });
  }

  // "▶ Start Export": runs whatever is already queued (built via the Overlay
  // tab's "+ Add to Queue", possibly across several sessions/laps) without
  // navigating away — the counterpart to editor.js's "Export Now", which
  // queues-and-starts a single session in one step from the Overlay tab.
  async function _startExport() {
    const items = State.get('selectedItems') || [];
    if (!items.length || _exporting) return;

    const btn = _container?.querySelector('#exp-start-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
    try {
      const [cfg, layout] = await Promise.all([API.getConfig(), API.getOverlay()]);
      const params = ExportParams.buildExportParams({ items, cfg, layout });
      State.set('exportPendingKeys', items.map(_queueItemKey));
      await API.startExport(params);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '▶ Start Export'; }
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────

  // Describes what a queued item will actually export, derived from its scope
  // (set once on the Overlay tab when it was queued) — not a stored label, so
  // it can never drift out of sync with the scope that's actually sent to Python.
  function _describeItem(item) {
    const scope = item.scope || 'selected_lap';
    if (scope === 'fastest')   return 'Fastest lap';
    if (scope === 'all_laps')  return 'All laps';
    if (scope === 'full')      return 'Full session';
    if (scope === 'lap_range') {
      return (item.lap_range_start != null && item.lap_range_end != null)
        ? `Laps ${item.lap_range_start}–${item.lap_range_end}` : 'Lap range';
    }
    const durStr = item.duration != null ? _fmtTime(item.duration) : '—';
    return `Lap ${item.lap_idx + 1}${item.is_best ? ' ★' : ''} — ${durStr}`;
  }

  function _refreshItemList(items) {
    if (!_container) return;
    const list  = _container.querySelector('#exp-item-list');
    const badge = _container.querySelector('#exp-item-count');
    if (!list) return;

    badge.textContent = items.length;
    _updateToolbarButtons(items);

    if (!items.length) {
      list.innerHTML = `
        <div class="empty-hint">No laps queued yet.</div>
        <button class="btn btn-sm btn-secondary" id="exp-goto-data-btn" style="margin-top:6px">Go to Data →</button>`;
      list.querySelector('#exp-goto-data-btn')?.addEventListener('click', () => Router.navigate('data'));
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
        const label = _describeItem(l);
        return `<span class="lap-chip${l.is_best ? ' best' : ''}"
                      title="${_esc(l.csv_path)}">
                  ${_esc(label)}
                  <button class="chip-remove" data-csv="${_esc(l.csv_path)}" data-lapidx="${l.lap_idx}" data-scope="${_esc(l.scope || 'selected_lap')}" title="Remove this lap">✕</button>
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
        const scope  = btn.dataset.scope;
        const current = State.get('selectedItems') || [];
        State.set('selectedItems', current.filter(i =>
          !(i.csv_path === csv && i.lap_idx === lapIdx && (i.scope || 'selected_lap') === scope)));
      });
    });
  }

  // ── Event handlers ────────────────────────────────────────────────────────────

  function _onProgress(detail) {
    // An export can start via the Overlay tab's "Export Now" before this page
    // has ever mounted — the first progress tick is what tells this page an
    // export is actually running.
    if (!_exporting) { _setExporting(true); _setBadge('Running', 'badge-run'); }
    // Python progress_cb sends 0–100 directly; clamp to avoid display glitches
    const pct = Math.min(100, Math.max(0, Math.round(detail.value || 0)));
    _setProgress(pct, detail.message || '');
  }

  function _onLog(detail) {
    if (!_exporting) { _setExporting(true); _setBadge('Running', 'badge-run'); }
    const msg = detail.message || '';
    // Always accumulate — even when the Export tab is not visible
    _logLines.push(msg);
    if (_logLines.length > 500) _logLines.shift();
    if (!_container) return;
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

    // Drop the items that just finished exporting from the queue — but only
    // those, since more may have been queued (e.g. from the Data page) while
    // this export was still running. Whichever page started the export
    // (this one's "Start Export" or the Overlay tab's "Export Now") records
    // the batch here via State.set('exportPendingKeys', ...) beforehand.
    const pendingKeys = State.get('exportPendingKeys');
    if (ok && pendingKeys) {
      const done = new Set(pendingKeys);
      State.set('selectedItems', (State.get('selectedItems') || []).filter(i => !done.has(_queueItemKey(i))));
    }
    State.set('exportPendingKeys', null);

    // Export finished — offer auto-sync the chance to run now.
    // Python's start_auto_sync skips sessions already synced or failed, so
    // it is safe to call with all matched sessions (it filters internally).
    const sessions = (State.get('sessions') || []).filter(s => s.matched && s.video_paths?.length);
    if (sessions.length) API.startAutoSync(sessions).catch(() => {});
  }

  // ── UI state helpers ──────────────────────────────────────────────────────────

  function _setExporting(active) {
    _exporting = active;
    if (!_container) return;
    const cancel = _container.querySelector('#exp-cancel-btn');
    if (cancel) cancel.classList.toggle('hidden', !active);
    _updateToolbarButtons(State.get('selectedItems') || []);
  }

  function _updateToolbarButtons(items) {
    if (!_container) return;
    const btn = _container.querySelector('#exp-start-btn');
    if (btn) btn.classList.toggle('hidden', _exporting || !items.length);
    const clearBtn = _container.querySelector('#exp-clear-btn');
    if (clearBtn) clearBtn.classList.toggle('hidden', _exporting || !items.length);
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
    _badgeText = text;
    _badgeCls  = cls || 'badge-dim';
    if (!_container) return;
    const badge = _container.querySelector('#exp-status-badge');
    if (!badge) return;
    badge.textContent = _badgeText;
    badge.className   = 'badge ' + _badgeCls;
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

  // ── Persistent export event listeners ────────────────────────────────────────
  // Registered once when the module loads — NOT tied to the Export tab being
  // visible. This ensures progress, log lines, and done state are captured even
  // when the user is on the Data, Overlay, or Settings tab during an export.
  // Whichever button starts an export (this page's "Start Export" or the
  // Overlay tab's "Export Now"), _exporting-equivalent state here is set via
  // these same push events, not directly by the click handler.
  API.on('export_progress', _onProgress);
  API.on('export_log',      _onLog);
  API.on('export_done',     _onDone);
})();
