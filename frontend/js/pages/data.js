/**
 * data.js — Data Selection page.
 *
 * Fixes vs original:
 *  - Sessions persist across navigation (no re-fetch on every mount)
 *  - Sorted newest-first; grouped by calendar period (Today/Yesterday/etc.)
 *  - Laps & best lap populated asynchronously per-session after scan
 *  - Video sync offset editable inline per session
 *  - Cleaner "Add to Export" UX: click chips to stage, one footer button to enqueue
 */
(function () {

  // ── Persistent module state (survives unmount/remount) ────────────────────────
  let _sessions    = [];   // flat list from scan_sessions, sorted newest-first
  let _lapMeta     = {};   // csv_path → {count, best}  (populated async)
  let _lapDetails  = {};   // csv_path → [{lap_idx, duration, is_best}]
  let _loading     = new Set();  // csv_paths currently loading laps
  let _expanded    = null; // csv_path of expanded row
  let _staged      = [];   // [{csv_path,lap_idx,…}] staged but not yet in export queue
  let _config      = null;
  let _scanning    = false;
  let _lastScanStatus = 'Ready — click Scan to find sessions.';

  // ── Utilities ──────────────────────────────────────────────────────────────────

  function fmtTime(secs) {
    if (secs == null || secs < 0) return '—';
    const m = Math.floor(secs / 60);
    const s = (secs % 60).toFixed(3).padStart(6, '0');
    return `${m}:${s}`;
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString(undefined,
        { year: 'numeric', month: 'short', day: 'numeric',
          hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  }

  function calendarGroup(iso) {
    if (!iso) return 'Unknown date';
    const d   = new Date(iso);
    const now = new Date();
    const diffDays = (now - d) / 86400000;
    if (diffDays < 1)                         return 'Today';
    if (diffDays < 2)                         return 'Yesterday';
    if (diffDays < 7)                         return 'This week';
    if (d.getFullYear() === now.getFullYear()) return d.toLocaleString(undefined, { month: 'long' });
    return String(d.getFullYear());
  }

  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function baseName(p) {
    return (p || '').replace(/\\/g, '/').split('/').pop() || p;
  }

  // ── Session grouping & sorting ─────────────────────────────────────────────────

  function sortedGrouped(sessions) {
    // Sort newest first
    const sorted = [...sessions].sort((a, b) => {
      const ta = a.csv_start ? new Date(a.csv_start).getTime() : 0;
      const tb = b.csv_start ? new Date(b.csv_start).getTime() : 0;
      return tb - ta;
    });

    // Group by calendar period
    const groups = [];
    let lastLabel = null;
    for (const s of sorted) {
      const label = calendarGroup(s.csv_start);
      if (label !== lastLabel) {
        groups.push({ label, sessions: [] });
        lastLabel = label;
      }
      groups[groups.length - 1].sessions.push(s);
    }
    return groups;
  }

  // ── Render ─────────────────────────────────────────────────────────────────────

  function statusBadge(s) {
    if (s.needs_conversion)       return `<span class="badge badge-warn">Needs conv.</span>`;
    if (!s.matched)               return `<span class="badge badge-muted">No video</span>`;
    if (s.sync_offset != null)    return `<span class="badge badge-ok">Synced</span>`;
    return `<span class="badge badge-warn">Unsynced</span>`;
  }

  function renderTable(container) {
    const tbody = container.querySelector('#sessions-tbody');
    if (!tbody) return;

    tbody.innerHTML = '';

    if (_sessions.length === 0) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td colspan="7" style="text-align:center; padding:32px; color:var(--text3)">
        No sessions found — configure folders in Settings then click Scan.</td>`;
      tbody.appendChild(tr);
      return;
    }

    const groups = sortedGrouped(_sessions);

    for (const group of groups) {
      // Group header row
      const hdr = document.createElement('tr');
      hdr.classList.add('group-header-row');
      hdr.innerHTML = `<td colspan="7" class="group-header">${esc(group.label)}</td>`;
      tbody.appendChild(hdr);

      for (const s of group.sessions) {
        tbody.appendChild(buildSessionRow(s, container));
        if (_expanded === s.csv_path) {
          tbody.appendChild(buildLapSubrow(s, container));
        }
      }
    }
  }

  function buildSessionRow(s, container) {
    const tr  = document.createElement('tr');
    tr.dataset.csvPath = s.csv_path;
    tr.classList.add('session-row');
    if (_staged.some(x => x.csv_path === s.csv_path)) tr.classList.add('selected');

    const meta    = _lapMeta[s.csv_path];
    const lapStr  = meta ? String(meta.count) : (_loading.has(s.csv_path) ? '…' : '—');
    const bestStr = meta ? fmtTime(meta.best)  : (_loading.has(s.csv_path) ? '…' : '—');
    const name    = baseName(s.csv_path);
    const isOpen  = _expanded === s.csv_path;

    tr.innerHTML = `
      <td class="expand-cell">${isOpen ? '▾' : '▸'}</td>
      <td class="cell-date">${fmtDate(s.csv_start)}</td>
      <td class="cell-name" title="${esc(s.csv_path)}">${esc(name)}</td>
      <td class="cell-source"><span class="source-tag">${esc(s.source || 'RaceBox')}</span></td>
      <td class="cell-num">${lapStr}</td>
      <td class="cell-num">${bestStr}</td>
      <td class="cell-status">${statusBadge(s)}</td>`;

    tr.addEventListener('click', () => toggleExpand(s, container));
    return tr;
  }

  function buildLapSubrow(s, container) {
    const tr = document.createElement('tr');
    tr.dataset.lapRow = s.csv_path;
    tr.classList.add('lap-subrow');

    const td = document.createElement('td');
    td.colSpan = 7;
    td.style.padding = '0';

    const laps = _lapDetails[s.csv_path];

    if (_loading.has(s.csv_path)) {
      td.innerHTML = `<div class="lap-tray"><span class="spinner"></span><span style="color:var(--text3);font-size:10px">Loading laps…</span></div>`;
    } else if (!laps || laps.length === 0) {
      td.innerHTML = `<div class="lap-tray"><span style="color:var(--text3);font-size:10px">No laps found in this file.</span></div>`;
    } else {
      const chips = laps.map(lap => {
        const isStaged = _staged.some(x => x.csv_path === s.csv_path && x.lap_idx === lap.lap_idx);
        return `<div class="lap-chip ${isStaged ? 'selected' : ''} ${lap.is_best ? 'best' : ''}"
                     data-csv="${esc(s.csv_path)}" data-lap="${lap.lap_idx}">
                  <span class="lc-num">L${lap.lap_idx + 1}</span>
                  <span class="lc-time">${fmtTime(lap.duration)}</span>
                  ${lap.is_best ? '<span class="lc-best">BEST</span>' : ''}
                </div>`;
      }).join('');

      const syncVal = s.sync_offset != null ? s.sync_offset.toFixed(2) : '';
      const hasVid  = s.matched && (s.video_paths || []).length > 0;

      td.innerHTML = `
        <div class="lap-tray">
          <div class="lap-chips-row">${chips}</div>
          <div class="lap-tray-footer">
            ${hasVid ? `
              <label class="sync-label">Video offset (s):
                <input type="number" class="sync-input" step="0.1"
                       value="${esc(syncVal)}" placeholder="0.00"
                       data-csv="${esc(s.csv_path)}" title="Sync offset: positive = video starts this many seconds after telemetry">
              </label>
              <button class="btn btn-secondary btn-sm sync-save-btn" data-csv="${esc(s.csv_path)}">Set Sync</button>
            ` : '<span style="color:var(--text3);font-size:9px">No video matched</span>'}
            <div style="flex:1"></div>
            <button class="btn btn-sm sel-all-btn" data-csv="${esc(s.csv_path)}">Select all</button>
            <button class="btn btn-accent btn-sm queue-btn" data-csv="${esc(s.csv_path)}">+ Add to Export</button>
          </div>
        </div>`;
    }

    tr.appendChild(td);

    // Wire chip clicks
    td.querySelectorAll('.lap-chip').forEach(chip => {
      chip.addEventListener('click', e => {
        e.stopPropagation();
        toggleStage(s, parseInt(chip.dataset.lap));
        refreshLapSubrow(s, container);
        refreshSessionRow(s, container);
        refreshFooter(container);
      });
    });

    // Select all
    const selAll = td.querySelector('.sel-all-btn');
    if (selAll) {
      selAll.addEventListener('click', e => {
        e.stopPropagation();
        const laps = _lapDetails[s.csv_path] || [];
        for (const lap of laps) {
          if (!_staged.some(x => x.csv_path === s.csv_path && x.lap_idx === lap.lap_idx)) {
            _staged.push(buildStagedItem(s, lap));
          }
        }
        refreshLapSubrow(s, container);
        refreshSessionRow(s, container);
        refreshFooter(container);
      });
    }

    // + Add to Export
    const queueBtn = td.querySelector('.queue-btn');
    if (queueBtn) {
      queueBtn.addEventListener('click', e => {
        e.stopPropagation();
        pushStagedToExport(s.csv_path, container);
      });
    }

    // Sync save
    const syncBtn = td.querySelector('.sync-save-btn');
    if (syncBtn) {
      syncBtn.addEventListener('click', async e => {
        e.stopPropagation();
        const input = td.querySelector(`.sync-input[data-csv]`);
        const val   = parseFloat(input?.value ?? '') || 0;
        // Persist via config
        await API.saveConfig({ offsets: { ...(_config?.offsets || {}), [s.csv_path]: val } });
        s.sync_offset = val;
        // Update badge
        refreshSessionRow(s, container);
        const msg = document.createElement('span');
        msg.style.cssText = 'color:var(--ok);font-size:9px;margin-left:6px';
        msg.textContent = 'Saved';
        syncBtn.after(msg);
        setTimeout(() => msg.remove(), 1500);
      });
    }

    return tr;
  }

  // ── Staging & export queue ─────────────────────────────────────────────────────

  function buildStagedItem(session, lap) {
    return {
      csv_path:    session.csv_path,
      lap_idx:     lap.lap_idx,
      source:      session.source,
      video_paths: session.video_paths || [],
      sync_offset: session.sync_offset,
      duration:    lap.duration,
      is_best:     lap.is_best,
    };
  }

  function toggleStage(session, lapIdx) {
    const idx = _staged.findIndex(x => x.csv_path === session.csv_path && x.lap_idx === lapIdx);
    if (idx >= 0) {
      _staged.splice(idx, 1);
    } else {
      const laps = _lapDetails[session.csv_path] || [];
      const lap  = laps.find(l => l.lap_idx === lapIdx);
      if (lap) _staged.push(buildStagedItem(session, lap));
    }
  }

  function pushStagedToExport(csvPath, container) {
    const toAdd  = _staged.filter(x => x.csv_path === csvPath);
    if (toAdd.length === 0) {
      // If nothing staged for this session, add best lap automatically
      const laps = _lapDetails[csvPath] || [];
      const best = laps.find(l => l.is_best) || laps[0];
      if (best) {
        const s = _sessions.find(s => s.csv_path === csvPath);
        if (s) toAdd.push(buildStagedItem(s, best));
      }
    }
    if (toAdd.length === 0) return;

    const existing = State.get('selectedItems') || [];
    const merged   = [...existing];
    for (const item of toAdd) {
      if (!merged.some(e => e.csv_path === item.csv_path && e.lap_idx === item.lap_idx)) {
        merged.push(item);
      }
    }
    State.set('selectedItems', merged);

    // Clear staging for this session
    _staged = _staged.filter(x => x.csv_path !== csvPath);
    refreshLapSubrow({ csv_path: csvPath, ..._sessions.find(s => s.csv_path === csvPath) }, container);
    refreshSessionRow(_sessions.find(s => s.csv_path === csvPath), container);
    refreshFooter(container);

    // Brief confirmation
    const footer = container.querySelector('#data-footer');
    if (footer) {
      const prev = footer.innerHTML;
      footer.innerHTML = `<span class="ok-flash">✓ ${toAdd.length} lap${toAdd.length !== 1 ? 's' : ''} added to export queue</span>`;
      setTimeout(() => refreshFooter(container), 1800);
    }
  }

  // ── Expand / collapse ──────────────────────────────────────────────────────────

  async function toggleExpand(session, container) {
    const wasOpen = _expanded === session.csv_path;
    _expanded = wasOpen ? null : session.csv_path;

    renderTable(container);

    if (!wasOpen && !_lapDetails[session.csv_path] && !_loading.has(session.csv_path)) {
      await loadLaps(session, container);
    }
  }

  async function loadLaps(session, container) {
    _loading.add(session.csv_path);
    refreshLapSubrow(session, container);

    try {
      const laps = await API.getLaps(session.csv_path);
      _lapDetails[session.csv_path] = laps;
      if (laps.length > 0) {
        const best = laps.filter(l => l.duration != null)
                         .reduce((b, l) => (l.duration < b ? l.duration : b), Infinity);
        _lapMeta[session.csv_path] = {
          count: laps.length,
          best:  isFinite(best) ? best : null,
        };
      }
    } catch (_) {
      _lapDetails[session.csv_path] = [];
    }

    _loading.delete(session.csv_path);
    // Refresh both the session row (to show count/best) and the lap tray
    refreshLapSubrow(session, container);
    refreshSessionRow(session, container);
  }

  // ── Partial DOM refreshes (avoid full redraw) ──────────────────────────────────

  function _findRow(container, attr, value) {
    // Use a loop instead of CSS.escape — Windows paths contain characters
    // (backslash, colon) that are tricky to escape for CSS attribute selectors.
    const rows = container.querySelectorAll(`tr[${attr}]`);
    for (const row of rows) {
      if (row.dataset[_camel(attr)] === value) return row;
    }
    return null;
  }

  function _camel(attrName) {
    // 'data-csv-path' → 'csvPath'  (matches element.dataset key)
    return attrName.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  }

  function refreshSessionRow(session, container) {
    if (!session) return;
    const old = _findRow(container, 'data-csv-path', session.csv_path);
    if (old) old.replaceWith(buildSessionRow(session, container));
  }

  function refreshLapSubrow(session, container) {
    if (!session) return;
    const old = _findRow(container, 'data-lap-row', session.csv_path);
    if (old) old.replaceWith(buildLapSubrow(session, container));
  }

  function refreshFooter(container) {
    const footer = container.querySelector('#data-footer');
    if (!footer) return;
    const n      = _staged.length;
    const queued = (State.get('selectedItems') || []).length;

    if (n === 0) {
      footer.innerHTML = `
        <span class="footer-hint">Click laps to stage them, then <strong>+ Add to Export</strong></span>
        ${queued > 0 ? `<span class="badge badge-ok">${queued} in export queue</span>` : ''}`;
    } else {
      footer.innerHTML = `
        <span class="footer-hint"><strong>${n}</strong> lap${n !== 1 ? 's' : ''} staged</span>
        <button class="btn btn-secondary btn-sm" id="footer-clear">Clear</button>
        <button class="btn btn-accent" id="footer-add">+ Add to Export</button>
        ${queued > 0 ? `<span class="badge badge-ok">${queued} in queue</span>` : ''}`;

      footer.querySelector('#footer-clear')?.addEventListener('click', () => {
        _staged = [];
        renderTable(container);
        refreshFooter(container);
      });

      footer.querySelector('#footer-add')?.addEventListener('click', () => {
        const existing = State.get('selectedItems') || [];
        const merged   = [...existing];
        for (const item of _staged) {
          if (!merged.some(e => e.csv_path === item.csv_path && e.lap_idx === item.lap_idx)) {
            merged.push(item);
          }
        }
        State.set('selectedItems', merged);
        _staged = [];
        renderTable(container);
        refreshFooter(container);
      });
    }
  }

  // ── Post-scan: populate lap counts/bests in background ────────────────────────

  async function enrichSessionsAsync(sessions, container) {
    // Load laps for all sessions concurrently in small batches
    const BATCH = 4;
    for (let i = 0; i < sessions.length; i += BATCH) {
      const batch = sessions.slice(i, i + BATCH);
      await Promise.all(batch.map(async s => {
        if (_lapMeta[s.csv_path]) return;
        try {
          _loading.add(s.csv_path);
          const laps = await API.getLaps(s.csv_path);
          _lapDetails[s.csv_path] = laps;
          if (laps.length > 0) {
            const bestDur = laps.filter(l => l.duration != null)
                               .reduce((b, l) => (l.duration < b ? l.duration : b), Infinity);
            _lapMeta[s.csv_path] = {
              count: laps.length,
              best:  isFinite(bestDur) ? bestDur : null,
            };
          }
        } catch (_) {}
        _loading.delete(s.csv_path);
      }));
      // Refresh visible rows after each batch
      renderTable(container);
    }
  }

  // ── Scan ───────────────────────────────────────────────────────────────────────

  async function doScan(container) {
    if (_scanning) return;
    _config = _config || await API.getConfig();
    const paths = _config?.all_telemetry_paths || [];

    if (paths.length === 0) {
      setStatus(container, 'No telemetry folders configured — go to Settings first.');
      return;
    }

    _scanning = true;
    _lapMeta  = {};
    _lapDetails = {};
    setStatus(container, 'Scanning…');
    container.querySelector('#scan-btn')?.setAttribute('disabled', '');

    try {
      const all = [];
      for (const p of paths) {
        const results = await API.scanSessions(p);
        all.push(...results);
      }
      _sessions = all;
      _expanded = null;
      _staged   = [];

      renderTable(container);
      _lastScanStatus = `${_sessions.length} session${_sessions.length !== 1 ? 's' : ''} found — loading lap details…`;
      setStatus(container, _lastScanStatus);

      // Populate laps/best in background
      enrichSessionsAsync(_sessions, container).then(() => {
        _lastScanStatus = `${_sessions.length} session${_sessions.length !== 1 ? 's' : ''} — ready.`;
        setStatus(container, _lastScanStatus);
      });

    } catch (e) {
      _lastScanStatus = 'Scan failed: ' + e;
      setStatus(container, _lastScanStatus);
    }

    _scanning = false;
    container.querySelector('#scan-btn')?.removeAttribute('disabled');
  }

  function setStatus(container, msg) {
    _lastScanStatus = msg;
    const el = container.querySelector('#scan-status');
    if (el) el.textContent = msg;
  }

  // ── Mount / Unmount ────────────────────────────────────────────────────────────

  async function mount(container) {
    container.innerHTML = `
<div class="page data-page">
  <div class="toolbar">
    <div class="toolbar-left">
      <span class="page-title">Data</span>
      <span class="status-text" id="scan-status">${esc(_lastScanStatus)}</span>
    </div>
    <div class="toolbar-right">
      <button class="btn btn-secondary" id="scan-btn">↺ Scan</button>
    </div>
  </div>
  <div class="page-divider"></div>

  <div class="data-table-wrap">
    <table class="data-table">
      <colgroup>
        <col style="width:24px">
        <col style="width:170px">
        <col>
        <col style="width:90px">
        <col style="width:52px">
        <col style="width:80px">
        <col style="width:90px">
      </colgroup>
      <thead>
        <tr>
          <th></th>
          <th>Date</th>
          <th>File</th>
          <th>Source</th>
          <th>Laps</th>
          <th>Best</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody id="sessions-tbody"></tbody>
    </table>
  </div>

  <div class="data-footer" id="data-footer">
    <span class="footer-hint">Click laps to stage them, then <strong>+ Add to Export</strong></span>
  </div>
</div>`;

    container.querySelector('#scan-btn').addEventListener('click', () => doScan(container));

    // If we already have sessions from a previous scan, show them immediately
    if (_sessions.length > 0) {
      renderTable(container);
      refreshFooter(container);
      return;
    }

    // First visit: try cache, then show ready state
    _config = await API.getConfig();
    try {
      const cached = await API.scanSessions('__cache__');
      if (cached && cached.length > 0) {
        _sessions = cached;
        renderTable(container);
        // Enrich from cache too
        enrichSessionsAsync(_sessions, container).then(() => {
          setStatus(container, `${_sessions.length} sessions (cached) — click Scan to refresh.`);
        });
        setStatus(container, `${_sessions.length} sessions (cached) — loading details…`);
        return;
      }
    } catch (_) {}

    setStatus(container, 'Ready — click Scan to find sessions.');
  }

  function unmount() {
    // Module state (_sessions, _lapMeta, etc.) is intentionally preserved
    // so navigating away and back doesn't lose the scan results.
  }

  Router.register('data', { mount, unmount });
})();
