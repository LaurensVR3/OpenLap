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
  let _unlistenFns = [];   // DOM-lifetime listeners only (State subscriptions)
  let _progressPct = 0;
  let _progressMsg = '';
  let _badgeText   = 'Idle';
  let _badgeCls    = 'badge-dim';

  // Lap range picker state
  let _rangePickerStart = null;  // selected start lap_num
  let _rangePickerEnd   = null;  // selected end lap_num
  let _rangePickerLaps  = [];    // [{lap_num, duration, is_best}] from loaded session

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

    // Restore persistent state accumulated while the tab was not visible
    const logEl = container.querySelector('#exp-log');
    if (logEl && _logLines.length) {
      logEl.value = _logLines.join('\n');
      logEl.scrollTop = logEl.scrollHeight;
    }
    _setExporting(_exporting);   // re-applies button/cancel visibility
    _setProgress(_progressPct, _progressMsg);
    _setBadge(_badgeText, _badgeCls);

    // State subscriptions — torn down on unmount (DOM lifetime only)
    _unlistenFns.push(State.on('selectedItems', newItems => {
      _refreshItemList(newItems);
      _updateStartBtn(newItems);
      // Reload lap list when session changes (reset picker state for new session)
      const newCsv = newItems[0]?.csv_path;
      const oldCsv = (State.get('selectedItems') || [])[0]?.csv_path;
      if (newCsv !== oldCsv) {
        _rangePickerStart = null;
        _rangePickerEnd   = null;
        _rangePickerLaps  = [];
      }
      const scope = _container?.querySelector('#exp-scope')?.value;
      if (scope === 'lap_range') _loadLapRange();
    }));
    _unlistenFns.push(State.on('previewSession', ps => {
      if (ps) State.set('selectedItems', [_itemFromPreview(ps)]);
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
          <div class="exp-range-row hidden" style="flex-direction:column;gap:4px;padding:2px 0 4px">
            <div id="exp-range-header"
                 style="font-size:10px;color:var(--text3);font-style:italic">
              Click a lap to set start, click another to set end
            </div>
            <div id="exp-lap-list"
                 style="max-height:170px;overflow-y:auto;border:1px solid var(--border);
                        border-radius:3px;background:var(--bg)">
              <div style="padding:8px;font-size:10px;color:var(--text3)">
                Select a session to see laps.
              </div>
            </div>
            <input type="hidden" id="exp-range-start" value="1">
            <input type="hidden" id="exp-range-end" value="">
          </div>
          <div class="form-row">
            <label>Scope</label>
            <select id="exp-scope" class="input-field">
              <option value="selected_lap">Selected Lap</option>
              <option value="lap_range">Lap Range (1 video)</option>
              <option value="fastest_lap">Fastest Lap</option>
              <option value="all_laps">All Laps</option>
              <option value="full">Full Session</option>
            </select>
          </div>
          <div class="form-row">
            <label>Overlay only (.mov)</label>
            <input type="checkbox" id="exp-overlay-only" class="input-checkbox" title="Export a transparent ProRes 4444 overlay — drop it over your source clip in DaVinci Resolve, Premiere or Final Cut.">
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

    // ── Lap range picker ──────────────────────────────────────────────────────

    function _renderLapList() {
      const listEl  = $('exp-lap-list');
      const hdrEl   = $('exp-range-header');
      if (!listEl) return;

      if (!_rangePickerLaps.length) {
        listEl.innerHTML = '<div style="padding:8px;font-size:10px;color:var(--text3)">No timed laps found.</div>';
        if (hdrEl) hdrEl.textContent = 'No timed laps found.';
        return;
      }

      const s = _rangePickerStart, e = _rangePickerEnd;

      if (hdrEl) {
        if (s == null) {
          hdrEl.textContent = 'Click a lap to set range start.';
        } else if (e == null) {
          hdrEl.textContent = `From: Lap ${s} — click another lap to set end.`;
        } else {
          hdrEl.textContent = `Range: Lap ${s} → Lap ${e}`;
        }
      }

      listEl.innerHTML = _rangePickerLaps.map(lap => {
        const inRange  = s != null && e != null && lap.lap_num >= s && lap.lap_num <= e;
        const isStart  = lap.lap_num === s;
        const isEnd    = lap.lap_num === e;
        const dur      = _fmtTime(lap.duration);

        let bg    = 'transparent';
        let color = 'inherit';
        let badge = '';
        if (isStart && isEnd) { bg = 'var(--acc)';   color = '#fff'; badge = '<span style="font-size:8px;opacity:.8;margin-left:4px">FROM·TO</span>'; }
        else if (isStart)     { bg = 'var(--acc)';   color = '#fff'; badge = '<span style="font-size:8px;opacity:.8;margin-left:4px">FROM</span>'; }
        else if (isEnd)       { bg = 'var(--acc)';   color = '#fff'; badge = '<span style="font-size:8px;opacity:.8;margin-left:4px">TO</span>'; }
        else if (inRange)     { bg = 'rgba(var(--acc-rgb,99,102,241),0.15)'; }

        return `<div class="exp-lap-item" data-num="${lap.lap_num}"
                     style="display:flex;justify-content:space-between;align-items:center;
                            padding:4px 8px;cursor:pointer;user-select:none;
                            background:${bg};color:${color};font-size:10px;
                            border-bottom:1px solid var(--border)">
                  <span>Lap ${lap.lap_num}${lap.is_best ? ' ★' : ''}${badge}</span>
                  <span style="font-variant-numeric:tabular-nums;color:${color === '#fff' ? '#fff' : 'var(--acc2)'}">${dur}</span>
                </div>`;
      }).join('');

      // Update hidden inputs
      const startInp = $('exp-range-start');
      const endInp   = $('exp-range-end');
      if (startInp) startInp.value = s ?? 1;
      if (endInp)   endInp.value   = e ?? '';

      // Wire clicks
      listEl.querySelectorAll('.exp-lap-item').forEach(row => {
        row.addEventListener('click', () => {
          const num = parseInt(row.dataset.num);
          if (_rangePickerStart == null || (_rangePickerStart != null && _rangePickerEnd != null)) {
            // No selection or complete selection: start fresh
            _rangePickerStart = num;
            _rangePickerEnd   = null;
          } else if (num > _rangePickerStart) {
            // Extend to end
            _rangePickerEnd = num;
          } else if (num === _rangePickerStart) {
            // Click same lap: make it a single-lap range
            _rangePickerEnd = num;
          } else {
            // Click before start: reset start
            _rangePickerStart = num;
            _rangePickerEnd   = null;
          }
          _renderLapList();
        });
      });
    }

    async function _loadLapRange() {
      const listEl = $('exp-lap-list');
      if (!listEl) return;
      const items = State.get('selectedItems') || [];
      const csvPath = items[0]?.csv_path;
      if (!csvPath) {
        _rangePickerLaps = [];
        _renderLapList();
        return;
      }
      listEl.innerHTML = '<div style="padding:8px;font-size:10px;color:var(--text3)">Loading…</div>';
      try {
        const all = await API.getLaps(csvPath);
        // Only timed laps (not outlap/inlap)
        _rangePickerLaps = (all || []).filter(l => !l.is_outlap && !l.is_inlap)
          .map(l => ({ lap_num: l.lap_num, duration: l.duration, is_best: l.is_best }));
        // Auto-select full range on first load
        if (_rangePickerLaps.length && _rangePickerStart == null) {
          _rangePickerStart = _rangePickerLaps[0].lap_num;
          _rangePickerEnd   = _rangePickerLaps[_rangePickerLaps.length - 1].lap_num;
        }
      } catch (e) {
        _rangePickerLaps = [];
      }
      _renderLapList();
    }

    // Show/hide scope-specific rows
    const _syncScopeRows = () => {
      const scope = $('exp-scope')?.value || 'selected_lap';
      _container.querySelectorAll('.exp-clip-row').forEach(r => {
        r.classList.toggle('hidden', scope !== 'clip');
      });
      _container.querySelectorAll('.exp-range-row').forEach(r => {
        r.classList.toggle('hidden', scope !== 'lap_range');
      });
      if (scope === 'lap_range') _loadLapRange();
    };
    _syncScopeRows();
    $('exp-scope').addEventListener('change', _syncScopeRows);

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

    const _rangeStart = parseInt($('exp-range-start')?.value) || 1;
    const _rangeEndRaw = $('exp-range-end')?.value?.trim();
    const _rangeEnd   = _rangeEndRaw ? parseInt(_rangeEndRaw) : null;

    const params = {
      items:            items,
      scope:            $('exp-scope')?.value    || 'selected_lap',
      clip_start_s:     parseFloat($('exp-clip-start')?.value) || 0,
      clip_end_s:       parseFloat($('exp-clip-end')?.value)   || 0,
      padding:          parseFloat($('exp-padding').value)    || 5.0,
      ref_mode:         layout.ref_mode          || 'none',
      ref_lap_csv_path: layout.ref_lap_csv_path  || '',
      ref_lap_num:      layout.ref_lap_num        || 0,
      encoder:          cfg.encoder              || 'libx264',
      crf:              cfg.crf                  ?? 18,
      workers:          cfg.workers              ?? 4,
      is_bike:          layout.is_bike           ?? false,
      show_map:         layout.show_map          ?? true,
      show_tel:         layout.show_tel          ?? true,
      export_path:      cfg.export_path          || '',
      overlay_only:     $('exp-overlay-only')?.checked || false,
      layout,
    };

    // For lap_range scope, embed range on every item
    if (params.scope === 'lap_range') {
      params.items = params.items.map(item => ({
        ...item,
        scope:           'lap_range',
        lap_range_start: _rangeStart,
        lap_range_end:   _rangeEnd,
      }));
    }

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
  API.on('export_progress', _onProgress);
  API.on('export_log',      _onLog);
  API.on('export_done',     _onDone);
})();
