/**
 * data.js — Data Selection page.
 *
 * Flow:
 *   1. On mount: load config, show last-scanned sessions from cache
 *   2. User can rescan or change folder
 *   3. Clicking a session row expands its lap list
 *   4. Clicking laps toggles them for export; "Add to Export" sends to shared state
 */
(function () {
  // ── Helpers ───────────────────────────────────────────────────────────────────
  function fmt_time(secs) {
    if (secs == null || secs < 0) return '—';
    const m = Math.floor(secs / 60);
    const s = (secs % 60).toFixed(3).padStart(6, '0');
    return `${m}:${s}`;
  }

  function fmt_date(iso) {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    } catch { return iso; }
  }

  // ── State ─────────────────────────────────────────────────────────────────────
  let _sessions    = [];  // list returned by scan_sessions
  let _expanded    = null; // csv_path of expanded session row
  let _lapData     = {}; // csv_path -> [{lap_idx, duration, is_best}]
  let _loadingLaps = new Set();
  let _selected    = []; // [{csv_path, lap_idx, source, video_paths, sync_offset}]
  let _config      = null;
  let _scanning    = false;

  // ── Render helpers ────────────────────────────────────────────────────────────
  function statusBadge(session) {
    if (session.needs_conversion) return `<span class="badge badge-warn">XRK</span>`;
    if (!session.matched)         return `<span class="badge badge-muted">No video</span>`;
    if (session.sync_offset != null) return `<span class="badge badge-ok">Synced</span>`;
    return `<span class="badge badge-warn">Unsynced</span>`;
  }

  function renderSessionRow(s, tbody) {
    const tr = document.createElement('tr');
    tr.dataset.csvPath = s.csv_path;
    tr.classList.toggle('selected', _selected.some(x => x.csv_path === s.csv_path));

    const dateStr = s.csv_start ? fmt_date(s.csv_start) : '—';
    const track   = s.track || '—';
    const laps    = s.laps  || '—';
    const best    = s.best  ? fmt_time(s.best) : '—';
    const vidIcon = s.matched ? '🎬' : '';

    tr.innerHTML = `
      <td style="width:28px; text-align:center; font-size:10px; color:var(--text3)">
        ${_expanded === s.csv_path ? '▾' : '▸'}
      </td>
      <td>${dateStr}</td>
      <td>${s.source || 'RaceBox'}</td>
      <td title="${s.csv_path}">${track}</td>
      <td>${laps}</td>
      <td>${best}</td>
      <td>${vidIcon}</td>
      <td>${statusBadge(s)}</td>
    `;

    tr.addEventListener('click', () => toggleExpand(s, tbody));
    return tr;
  }

  function renderLapRow(s) {
    const laps = _lapData[s.csv_path] || [];
    const div = document.createElement('tr');
    div.dataset.lapRow = s.csv_path;

    const td = document.createElement('td');
    td.colSpan = 8;
    td.style.padding = '0';

    if (_loadingLaps.has(s.csv_path)) {
      td.innerHTML = `<div class="lap-list"><span class="spinner"></span></div>`;
    } else if (laps.length === 0) {
      td.innerHTML = `<div class="lap-list" style="color:var(--text3); font-size:10px">No laps found</div>`;
    } else {
      const chips = laps.map(lap => {
        const isSelected = _selected.some(x =>
          x.csv_path === s.csv_path && x.lap_idx === lap.lap_idx);
        return `
          <div class="lap-chip ${isSelected ? 'selected' : ''}"
               data-csv="${s.csv_path}" data-lap="${lap.lap_idx}">
            <span>Lap ${lap.lap_idx + 1}</span>
            <span class="lap-time">${fmt_time(lap.duration)}</span>
            ${lap.is_best ? '<span class="lap-best">BEST</span>' : ''}
          </div>`;
      }).join('');

      td.innerHTML = `
        <div class="lap-list">
          ${chips}
          <div style="flex:1"></div>
          <button class="btn btn-sm btn-accent add-to-export-btn"
                  data-csv="${s.csv_path}" style="margin-left:8px">
            Add to Export ▶
          </button>
        </div>`;
    }

    div.appendChild(td);

    // Wire lap chip clicks
    td.querySelectorAll('.lap-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const csvPath = chip.dataset.csv;
        const lapIdx  = parseInt(chip.dataset.lap);
        toggleLap(s, lapIdx);
      });
    });

    // Wire Add to Export button
    const addBtn = td.querySelector('.add-to-export-btn');
    if (addBtn) {
      addBtn.addEventListener('click', e => {
        e.stopPropagation();
        pushToExport(s.csv_path);
      });
    }

    return div;
  }

  function toggleLap(session, lapIdx) {
    const idx = _selected.findIndex(x =>
      x.csv_path === session.csv_path && x.lap_idx === lapIdx);
    if (idx >= 0) {
      _selected.splice(idx, 1);
    } else {
      const laps = _lapData[session.csv_path] || [];
      const lap  = laps.find(l => l.lap_idx === lapIdx);
      _selected.push({
        csv_path:     session.csv_path,
        lap_idx:      lapIdx,
        source:       session.source,
        video_paths:  session.video_paths || [],
        sync_offset:  session.sync_offset,
        duration:     lap ? lap.duration : null,
        is_best:      lap ? lap.is_best : false,
      });
    }
    redrawLapRow(session);
    redrawSelectionFooter();
  }

  function pushToExport(csvPath) {
    const existing = State.get('selectedItems') || [];
    // Add any newly selected laps that aren't already in the export queue
    const toAdd = _selected.filter(x =>
      x.csv_path === csvPath &&
      !existing.some(e => e.csv_path === x.csv_path && e.lap_idx === x.lap_idx));
    State.set('selectedItems', [...existing, ...toAdd]);

    // Show brief confirmation
    const footer = document.getElementById('selection-footer');
    if (footer) {
      footer.innerHTML = `<span style="color:var(--ok)">✓ Added to export queue</span>`;
      setTimeout(() => redrawSelectionFooter(), 1500);
    }
  }

  async function toggleExpand(session, tbody) {
    const wasExpanded = _expanded === session.csv_path;
    _expanded = wasExpanded ? null : session.csv_path;

    // Re-render all session rows + lap rows
    redrawTable(tbody, _sessions);

    if (!wasExpanded && !_lapData[session.csv_path]) {
      // Load lap data
      _loadingLaps.add(session.csv_path);
      redrawLapSubrow(session, tbody);

      try {
        const laps = await API.getLaps(session.csv_path);
        _lapData[session.csv_path] = laps;
      } catch (e) {
        _lapData[session.csv_path] = [];
      }
      _loadingLaps.delete(session.csv_path);
      redrawLapSubrow(session, tbody);
    }
  }

  function redrawTable(tbody, sessions) {
    tbody.innerHTML = '';
    sessions.forEach(s => {
      tbody.appendChild(renderSessionRow(s, tbody));
      if (_expanded === s.csv_path) {
        tbody.appendChild(renderLapRow(s));
      }
    });
  }

  function redrawLapRow(session) {
    const lapRow = document.querySelector(`tr[data-lap-row="${CSS.escape(session.csv_path)}"]`);
    if (lapRow) {
      const newRow = renderLapRow(session);
      lapRow.replaceWith(newRow);
    }
  }

  function redrawLapSubrow(session, tbody) {
    const existing = tbody.querySelector(`tr[data-lap-row="${CSS.escape(session.csv_path)}"]`);
    const newRow = renderLapRow(session);
    if (existing) {
      existing.replaceWith(newRow);
    } else {
      // Insert after the session row
      const sessionRow = tbody.querySelector(`tr[data-csv-path="${CSS.escape(session.csv_path)}"]`);
      if (sessionRow) sessionRow.after(newRow);
    }
  }

  function redrawSelectionFooter() {
    const footer = document.getElementById('selection-footer');
    if (!footer) return;
    const n = _selected.length;
    if (n === 0) {
      footer.innerHTML = `<span style="color:var(--text3)">Click laps to select, then Add to Export</span>`;
    } else {
      footer.innerHTML = `
        <span style="color:var(--text2)">${n} lap${n > 1 ? 's' : ''} selected</span>
        <button class="btn btn-sm btn-accent" id="add-all-btn">Add to Export ▶</button>
        <button class="btn btn-sm" id="clear-sel-btn">Clear</button>`;
      document.getElementById('add-all-btn')?.addEventListener('click', () => {
        const existing = State.get('selectedItems') || [];
        const toAdd = _selected.filter(x =>
          !existing.some(e => e.csv_path === x.csv_path && e.lap_idx === x.lap_idx));
        State.set('selectedItems', [...existing, ...toAdd]);
        _selected = [];
        redrawSelectionFooter();
      });
      document.getElementById('clear-sel-btn')?.addEventListener('click', () => {
        _selected = [];
        if (_expanded) {
          const s = _sessions.find(x => x.csv_path === _expanded);
          if (s) redrawLapRow(s);
        }
        redrawSelectionFooter();
      });
    }
  }

  // ── Scan ──────────────────────────────────────────────────────────────────────
  async function doScan() {
    if (_scanning) return;
    const paths = _config ? _config.all_telemetry_paths || [] : [];
    if (paths.length === 0) {
      setStatus('No telemetry folder configured. Go to Settings first.');
      return;
    }

    _scanning = true;
    setStatus('Scanning…');
    document.getElementById('scan-btn')?.setAttribute('disabled', '');

    try {
      // Scan each configured path
      const allSessions = [];
      for (const p of paths) {
        const results = await API.scanSessions(p);
        allSessions.push(...results);
      }
      _sessions = allSessions;
      _lapData  = {};
      _expanded = null;

      const tbody = document.getElementById('sessions-tbody');
      if (tbody) redrawTable(tbody, _sessions);

      setStatus(`${_sessions.length} session${_sessions.length !== 1 ? 's' : ''} found.`);
    } catch (e) {
      setStatus('Scan failed: ' + e);
    }

    _scanning = false;
    document.getElementById('scan-btn')?.removeAttribute('disabled');
  }

  function setStatus(msg) {
    const el = document.getElementById('scan-status');
    if (el) el.textContent = msg;
  }

  // ── Mount / unmount ───────────────────────────────────────────────────────────
  async function mount(container) {
    container.innerHTML = `
      <div class="page" id="data-page">
        <div class="toolbar">
          <div class="toolbar-left">
            <span class="page-title">Data Selection</span>
            <span class="status-text" id="scan-status">Loading…</span>
          </div>
          <div class="toolbar-right">
            <button class="btn btn-accent" id="scan-btn">↺ Scan</button>
          </div>
        </div>
        <div class="page-divider"></div>

        <div style="overflow-x:auto; flex:1;">
          <table class="data-table" style="width:100%">
            <thead>
              <tr>
                <th></th>
                <th>Date</th>
                <th>Source</th>
                <th>Track</th>
                <th>Laps</th>
                <th>Best</th>
                <th></th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody id="sessions-tbody"></tbody>
          </table>
        </div>

        <div id="empty-data" class="empty-state" style="display:none">
          <div class="empty-icon">📂</div>
          <div class="empty-title">No sessions found</div>
          <div>Configure a telemetry folder in Settings and click Scan.</div>
        </div>

        <!-- Footer bar: lap selection summary -->
        <div style="
          border-top: 1px solid var(--border);
          padding: 8px 24px;
          display: flex;
          align-items: center;
          gap: 12px;
          min-height: 38px;
          background: var(--sidebar);
        " id="selection-footer">
          <span style="color:var(--text3)">Click laps to select, then Add to Export</span>
        </div>
      </div>`;

    // Load config
    _config = await API.getConfig();

    document.getElementById('scan-btn').addEventListener('click', doScan);

    // Try to restore cached sessions from Python
    try {
      const cached = await API.scanSessions('__cache__');
      if (cached && cached.length > 0) {
        _sessions = cached;
        const tbody = document.getElementById('sessions-tbody');
        if (tbody) redrawTable(tbody, _sessions);
        setStatus(`${_sessions.length} session${_sessions.length !== 1 ? 's' : ''} (cached). Click Scan to refresh.`);
        return;
      }
    } catch (_) {}

    setStatus('Ready. Click Scan to find sessions.');
  }

  function unmount() {}

  Router.register('data', { mount, unmount });
})();
