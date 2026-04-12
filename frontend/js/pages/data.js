/**
 * data.js — Data Selection page.
 *
 * Layout: sessions list (left, scrollable) | detail + sync panel (right, fixed 340px)
 *
 * Flow:
 *  1. Mount: load cache immediately, auto-trigger background scan
 *  2. Sessions grouped by actual date (YYYY-MM-DD), newest-first
 *  3. Click session → right panel shows info, lap chips, video sync
 *  4. Lap chips → staged, then "Add to Export" queues them in State
 *  5. Align video: <video> element + scrub slider + mark button → saves offset
 */
(function () {

  // ── Persistent module state ───────────────────────────────────────────────────
  let _sessions   = [];   // flat list, sorted newest-first
  let _meta       = {};   // csv_path → {track, laps, best}
  let _lapDetails = {};   // csv_path → [{lap_idx, duration, is_best}]
  let _staged     = [];   // lap items staged for export
  let _selCsv     = null; // currently selected session csv_path
  let _config     = null;
  let _scanning   = false;
  let _statusMsg  = '';
  let _container  = null;
  let _metaQueue  = [];   // sessions waiting for meta fetch
  let _metaBusy   = false;

  // Best per day: csv_path → true if this session has the day's best lap
  let _dayBest = {};

  // ── Utilities ─────────────────────────────────────────────────────────────────

  function fmtTime(secs) {
    if (secs == null || secs < 0 || isNaN(secs)) return '—';
    const m = Math.floor(secs / 60);
    const s = (secs % 60).toFixed(3).padStart(6, '0');
    return `${m}:${s}`;
  }

  function fmtDateTime(iso) {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString(undefined,
        { year:'numeric', month:'2-digit', day:'2-digit',
          hour:'2-digit', minute:'2-digit' });
    } catch { return iso; }
  }

  function dateKey(iso) {
    if (!iso) return 'Unknown';
    try { return new Date(iso).toISOString().slice(0, 10); }
    catch { return 'Unknown'; }
  }

  function esc(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function baseName(p) {
    return (p||'').replace(/\\/g,'/').split('/').pop() || p;
  }

  function fileUrl(winPath) {
    return 'file:///' + winPath.replace(/\\/g, '/');
  }

  // ── Compute day-best ──────────────────────────────────────────────────────────

  function recomputeDayBest() {
    _dayBest = {};
    const byDay = {};
    for (const s of _sessions) {
      const d = dateKey(s.csv_start);
      if (!byDay[d]) byDay[d] = [];
      const m = _meta[s.csv_path];
      if (m && m.best_secs != null) byDay[d].push({ csv: s.csv_path, best: m.best_secs });
    }
    for (const entries of Object.values(byDay)) {
      if (!entries.length) continue;
      entries.sort((a, b) => a.best - b.best);
      _dayBest[entries[0].csv] = true;
    }
  }

  // ── Session grouping ──────────────────────────────────────────────────────────

  function sortedGroups() {
    const sorted = [..._sessions].sort((a, b) => {
      const ta = a.csv_start ? new Date(a.csv_start).getTime() : 0;
      const tb = b.csv_start ? new Date(b.csv_start).getTime() : 0;
      return tb - ta;
    });
    const groups = [];
    let lastDay = null;
    for (const s of sorted) {
      const day = dateKey(s.csv_start);
      if (day !== lastDay) { groups.push({ day, sessions: [] }); lastDay = day; }
      groups[groups.length - 1].sessions.push(s);
    }
    return groups;
  }

  // ── Left panel: session list ──────────────────────────────────────────────────

  function renderLeft() {
    const pane = _container?.querySelector('#data-left');
    if (!pane) return;

    if (_sessions.length === 0) {
      pane.innerHTML = `<div class="dl-empty">No sessions — configure folders in Settings and click Scan.</div>`;
      return;
    }

    const groups = sortedGroups();
    pane.innerHTML = groups.map(g => `
      <div class="dl-day-hdr">${esc(g.day)}</div>
      ${g.sessions.map(s => sessionRow(s)).join('')}
    `).join('');

    pane.querySelectorAll('.dl-row').forEach(row => {
      row.addEventListener('click', () => selectSession(row.dataset.csv));
    });
  }

  function sessionRow(s) {
    const m       = _meta[s.csv_path] || {};
    const isSel   = s.csv_path === _selCsv;
    const isDayB  = _dayBest[s.csv_path];
    const time    = s.csv_start ? new Date(s.csv_start)
                      .toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}) : '—';
    const track   = m.track || baseName(s.csv_path);
    const lapStr  = m.laps  || '—';
    const bestStr = m.best  || (m.best_secs != null ? fmtTime(m.best_secs) : '—');
    const icon    = s.needs_conversion ? '⟳'
                  : (!s.matched)       ? '✗'
                  : (s.sync_offset != null) ? '✓' : '≈';
    const iconCls = s.needs_conversion ? 'di-pending'
                  : (!s.matched)       ? 'di-novid'
                  : (s.sync_offset != null) ? 'di-synced' : 'di-unsync';

    return `<div class="dl-row${isSel?' sel':''}${isDayB?' day-best':''}" data-csv="${esc(s.csv_path)}">
      <span class="dl-icon ${iconCls}">${icon}</span>
      <span class="dl-time">${esc(time)}</span>
      <span class="dl-track" title="${esc(s.csv_path)}">${esc(track)}</span>
      <span class="dl-source">${esc(s.source||'RaceBox')}</span>
      <span class="dl-num">${esc(lapStr)}</span>
      <span class="dl-num">${esc(bestStr)}</span>
    </div>`;
  }

  function selectSession(csvPath) {
    _selCsv = csvPath;
    // Update selection highlight
    _container?.querySelectorAll('.dl-row').forEach(r => {
      r.classList.toggle('sel', r.dataset.csv === csvPath);
    });
    renderRight();

    // Load laps if not cached
    const s = _sessions.find(x => x.csv_path === csvPath);
    if (s && !_lapDetails[csvPath]) loadLaps(s);
  }

  // ── Right panel: session detail + sync ────────────────────────────────────────

  function renderRight() {
    const pane = _container?.querySelector('#data-right');
    if (!pane) return;

    const s = _sessions.find(x => x.csv_path === _selCsv);
    if (!s) {
      pane.innerHTML = `<div class="dr-empty">Select a session to see details and align video.</div>`;
      return;
    }

    const m    = _meta[s.csv_path] || {};
    const laps = _lapDetails[s.csv_path];
    const off  = s.sync_offset;

    // Session info
    const vidPaths = s.video_paths || [];
    const hasVid   = s.matched && vidPaths.length > 0;

    pane.innerHTML = `
<!-- Info card -->
<div class="dr-card">
  <div class="dr-card-title">SESSION INFO</div>
  <div class="dr-rows">
    <div class="dr-row"><span class="dr-lbl">Source</span><span class="dr-val">${esc(s.source||'RaceBox')}</span></div>
    <div class="dr-row"><span class="dr-lbl">Track</span><span class="dr-val dr-track-val">${esc(m.track||'—')}</span></div>
    <div class="dr-row"><span class="dr-lbl">Date</span><span class="dr-val">${esc(fmtDateTime(s.csv_start))}</span></div>
    <div class="dr-row"><span class="dr-lbl">Laps</span><span class="dr-val">${esc(m.laps||'—')}</span></div>
    <div class="dr-row"><span class="dr-lbl">Best</span><span class="dr-val" style="color:var(--ok)">${esc(m.best||'—')}</span></div>
    <div class="dr-row"><span class="dr-lbl">Video</span><span class="dr-val ${hasVid?'':'dr-warn'}">${hasVid ? `✓ ${vidPaths.length} clip(s)` : '✗ No match'}</span></div>
    <div class="dr-row"><span class="dr-lbl">Offset</span><span class="dr-val ${off!=null?'':'dr-warn'}" id="dr-off-display">${off!=null ? off.toFixed(3)+'s ✓' : 'not set'}</span></div>
  </div>
  <div class="dr-actions">
    <label class="dr-mode-label">Mode:
      <select class="input-field dr-mode-sel" id="dr-bike-sel">
        <option value="car">Car</option>
        <option value="bike"${(s.is_bike?' selected':'')}>Bike</option>
      </select>
    </label>
  </div>
</div>

<!-- Lap chips -->
<div class="dr-card">
  <div class="dr-card-title">EXPORT LAPS</div>
  <div id="dr-laps">${renderLapChips(s)}</div>
  <div class="dr-actions">
    <button class="btn btn-sm" id="dr-sel-all">All</button>
    <button class="btn btn-sm" id="dr-sel-best">Best</button>
    <button class="btn btn-accent btn-sm" id="dr-add-export">+ Add to Export</button>
  </div>
</div>

<!-- Video align -->
${hasVid ? renderAlignCard(s, vidPaths, off) : ''}
`;

    wirePropPanel(s, pane);
  }

  function renderLapChips(s) {
    const laps = _lapDetails[s.csv_path];
    if (!laps) return `<div class="dr-loading"><span class="spinner"></span> Loading…</div>`;
    if (laps.length === 0) return `<div class="dr-hint">No laps found in this file.</div>`;
    return `<div class="dr-chip-row">` + laps.map(l => {
      const isSel = _staged.some(x => x.csv_path === s.csv_path && x.lap_idx === l.lap_idx);
      return `<div class="dr-chip${isSel?' sel':''}${l.is_best?' best':''}"
                   data-csv="${esc(s.csv_path)}" data-lap="${l.lap_idx}">
                <span class="drc-n">L${l.lap_idx+1}</span>
                <span class="drc-t">${fmtTime(l.duration)}</span>
                ${l.is_best?'<span class="drc-b">★</span>':''}
              </div>`;
    }).join('') + `</div>`;
  }

  function renderAlignCard(s, vidPaths, off) {
    const offVal = off != null ? off.toFixed(3) : '';
    return `
<div class="dr-card dr-align-card">
  <div class="dr-card-title">ALIGN VIDEO</div>
  <video id="sync-video" class="sync-video" preload="metadata"
         src="${esc(fileUrl(vidPaths[0]))}"></video>
  <div class="sync-controls">
    <button class="btn btn-sm" id="sv-mm">◀◀ −1s</button>
    <button class="btn btn-sm" id="sv-m">◀ −1f</button>
    <button class="btn btn-sm" id="sv-p">▶ +1f</button>
    <button class="btn btn-sm" id="sv-pp">▶▶ +1s</button>
    <span class="sync-time" id="sv-time">0:00.000</span>
  </div>
  <input type="range" id="sv-scrub" class="sync-scrub" min="0" max="1000" value="0" step="1">
  <div class="sync-mark-row">
    <button class="btn btn-ok btn-sm" id="sv-mark">🏁 Mark Lap 1 start</button>
    <span class="sync-mark-val" id="sv-mark-val">${off!=null ? 'Marked — offset: '+off.toFixed(3)+'s' : ''}</span>
  </div>
  <div class="sync-offset-row">
    <label style="font-size:9px;color:var(--text3)">Manual offset (s):</label>
    <input type="number" id="sv-off-input" class="input-field input-narrow sync-off-input"
           step="0.001" value="${esc(offVal)}" placeholder="0.000">
    <button class="btn btn-sm" id="sv-save">Save</button>
  </div>
  ${vidPaths.length > 1 ? `<div style="font-size:9px;color:var(--text3);margin-top:4px">+ ${vidPaths.length-1} more clip(s)</div>` : ''}
</div>`;
  }

  function wirePropPanel(s, pane) {
    // Lap chips
    pane.querySelectorAll('.dr-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        toggleStage(s, parseInt(chip.dataset.lap));
        const lapsEl = pane.querySelector('#dr-laps');
        if (lapsEl) lapsEl.innerHTML = renderLapChips(s);
        wireLapChips(s, pane);
        refreshFooter();
      });
    });

    // Select all / best
    pane.querySelector('#dr-sel-all')?.addEventListener('click', () => {
      const laps = _lapDetails[s.csv_path] || [];
      for (const l of laps) {
        if (!_staged.some(x => x.csv_path === s.csv_path && x.lap_idx === l.lap_idx))
          _staged.push(makeStagedItem(s, l));
      }
      const lapsEl = pane.querySelector('#dr-laps');
      if (lapsEl) lapsEl.innerHTML = renderLapChips(s);
      wireLapChips(s, pane);
      refreshFooter();
    });

    pane.querySelector('#dr-sel-best')?.addEventListener('click', () => {
      const laps  = _lapDetails[s.csv_path] || [];
      const best  = laps.find(l => l.is_best) || laps[0];
      if (best && !_staged.some(x => x.csv_path === s.csv_path && x.lap_idx === best.lap_idx))
        _staged.push(makeStagedItem(s, best));
      const lapsEl = pane.querySelector('#dr-laps');
      if (lapsEl) lapsEl.innerHTML = renderLapChips(s);
      wireLapChips(s, pane);
      refreshFooter();
    });

    // Add to export
    pane.querySelector('#dr-add-export')?.addEventListener('click', () => {
      // If nothing staged for this session, auto-add best
      let toAdd = _staged.filter(x => x.csv_path === s.csv_path);
      if (toAdd.length === 0) {
        const laps = _lapDetails[s.csv_path] || [];
        const best = laps.find(l => l.is_best) || laps[0];
        if (best) toAdd = [makeStagedItem(s, best)];
      }
      if (!toAdd.length) return;
      const existing = State.get('selectedItems') || [];
      const merged   = [...existing];
      for (const item of toAdd) {
        if (!merged.some(e => e.csv_path === item.csv_path && e.lap_idx === item.lap_idx))
          merged.push(item);
      }
      State.set('selectedItems', merged);
      _staged = _staged.filter(x => x.csv_path !== s.csv_path);
      const lapsEl = pane.querySelector('#dr-laps');
      if (lapsEl) lapsEl.innerHTML = renderLapChips(s);
      wireLapChips(s, pane);
      refreshFooter();
      flashFooter(`✓ ${toAdd.length} lap${toAdd.length!==1?'s':''} added to export queue`);
    });

    // Bike mode
    pane.querySelector('#dr-bike-sel')?.addEventListener('change', async e => {
      s.is_bike = e.target.value === 'bike';
      const cfg = await API.getConfig();
      const overrides = { ...(cfg.bike_overrides || {}) };
      overrides[s.csv_path] = s.is_bike;
      await API.saveConfig({ bike_overrides: overrides });
    });

    // Video sync
    wireVideoSync(s, pane);
  }

  function wireLapChips(s, pane) {
    pane.querySelectorAll('.dr-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        toggleStage(s, parseInt(chip.dataset.lap));
        const lapsEl = pane.querySelector('#dr-laps');
        if (lapsEl) lapsEl.innerHTML = renderLapChips(s);
        wireLapChips(s, pane);
        refreshFooter();
      });
    });
  }

  function wireVideoSync(s, pane) {
    const video  = pane.querySelector('#sync-video');
    const scrub  = pane.querySelector('#sv-scrub');
    const timeEl = pane.querySelector('#sv-time');
    const markEl = pane.querySelector('#sv-mark-val');
    const offInp = pane.querySelector('#sv-off-input');

    if (!video) return;

    let fps = 30; // default; will be updated from metadata

    function fmtVTime(t) {
      const m = Math.floor(t / 60);
      const s = (t % 60).toFixed(3).padStart(6, '0');
      return `${m}:${s}`;
    }

    video.addEventListener('loadedmetadata', () => {
      scrub.max = Math.round(video.duration * 1000);
      fps = 30; // estimate; HTML video doesn't expose fps reliably
    });

    video.addEventListener('timeupdate', () => {
      if (!video.seeking) {
        scrub.value = Math.round(video.currentTime * 1000);
        if (timeEl) timeEl.textContent = fmtVTime(video.currentTime);
      }
    });

    scrub?.addEventListener('input', () => {
      video.currentTime = scrub.value / 1000;
      if (timeEl) timeEl.textContent = fmtVTime(video.currentTime);
    });

    function step(frameDelta) {
      // frameDelta in frames (positive/negative); if > 20 treat as seconds
      const dt = Math.abs(frameDelta) > 10 ? (frameDelta > 0 ? 1 : -1) : frameDelta / fps;
      video.currentTime = Math.max(0, Math.min(video.duration || 0, video.currentTime + dt));
    }

    pane.querySelector('#sv-mm')?.addEventListener('click', () => step(-fps));
    pane.querySelector('#sv-m')?.addEventListener ('click', () => step(-1));
    pane.querySelector('#sv-p')?.addEventListener ('click', () => step(1));
    pane.querySelector('#sv-pp')?.addEventListener('click', () => step(fps));

    // Mark: save current video time as the lap-1-start moment
    pane.querySelector('#sv-mark')?.addEventListener('click', async () => {
      const markT  = video.currentTime;
      // offset = video time at which lap 1 starts = we'll use it as is
      // The export pipeline subtracts this from the video so it starts at the mark
      const offset = markT;
      s.sync_offset = offset;
      if (offInp) offInp.value = offset.toFixed(3);
      if (markEl) markEl.textContent = `Marked at ${fmtVTime(markT)} → offset ${offset.toFixed(3)}s`;
      await saveOffset(s);
      // Update display in left panel and info card
      renderLeft();
      const dispEl = pane.querySelector('#dr-off-display');
      if (dispEl) { dispEl.textContent = offset.toFixed(3)+'s ✓'; dispEl.className = 'dr-val'; }
    });

    // Manual offset save
    pane.querySelector('#sv-save')?.addEventListener('click', async () => {
      const val = parseFloat(offInp?.value ?? '') || 0;
      s.sync_offset = val;
      await saveOffset(s);
      if (markEl) markEl.textContent = `Offset ${val.toFixed(3)}s saved ✓`;
      renderLeft();
      const dispEl = pane.querySelector('#dr-off-display');
      if (dispEl) { dispEl.textContent = val.toFixed(3)+'s ✓'; dispEl.className = 'dr-val'; }
    });
  }

  async function saveOffset(s) {
    const offsets = { ...(_config?.offsets || {}), [s.csv_path]: s.sync_offset };
    _config = { ..._config, offsets };
    await API.saveConfig({ offsets });
  }

  // ── Staging ───────────────────────────────────────────────────────────────────

  function makeStagedItem(session, lap) {
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
      const lap = (_lapDetails[session.csv_path]||[]).find(l => l.lap_idx === lapIdx);
      if (lap) _staged.push(makeStagedItem(session, lap));
    }
  }

  // ── Footer ────────────────────────────────────────────────────────────────────

  function refreshFooter() {
    const footer = _container?.querySelector('#data-footer');
    if (!footer) return;
    const n      = _staged.length;
    const queued = (State.get('selectedItems')||[]).length;
    if (n === 0) {
      footer.innerHTML = `
        <span class="footer-hint">Select a session, pick laps, click <strong>+ Add to Export</strong></span>
        ${queued>0 ? `<span class="badge badge-ok">${queued} in export queue</span>` : ''}`;
    } else {
      footer.innerHTML = `
        <span class="footer-hint"><strong>${n}</strong> lap${n!==1?'s':''} staged</span>
        <button class="btn btn-secondary btn-sm" id="ftr-clear">Clear</button>
        <button class="btn btn-accent" id="ftr-add">+ Add to Export</button>
        ${queued>0 ? `<span class="badge badge-ok">${queued} in queue</span>` : ''}`;
      footer.querySelector('#ftr-clear')?.addEventListener('click', () => {
        _staged = [];
        refreshFooter();
        const pane = _container?.querySelector('#data-right');
        const s    = _sessions.find(x => x.csv_path === _selCsv);
        if (s && pane) {
          const lapsEl = pane.querySelector('#dr-laps');
          if (lapsEl) lapsEl.innerHTML = renderLapChips(s);
          wireLapChips(s, pane);
        }
      });
      footer.querySelector('#ftr-add')?.addEventListener('click', () => {
        const existing = State.get('selectedItems') || [];
        const merged   = [...existing];
        for (const item of _staged) {
          if (!merged.some(e => e.csv_path === item.csv_path && e.lap_idx === item.lap_idx))
            merged.push(item);
        }
        State.set('selectedItems', merged);
        _staged = [];
        refreshFooter();
      });
    }
  }

  function flashFooter(msg) {
    const footer = _container?.querySelector('#data-footer');
    if (!footer) return;
    const prev = footer.innerHTML;
    footer.innerHTML = `<span class="ok-flash">${esc(msg)}</span>`;
    setTimeout(() => refreshFooter(), 1800);
  }

  // ── Lap loading ───────────────────────────────────────────────────────────────

  async function loadLaps(session) {
    if (_lapDetails[session.csv_path]) { renderRight(); return; }
    try {
      const laps = await API.getLaps(session.csv_path);
      _lapDetails[session.csv_path] = laps;
    } catch (_) {
      _lapDetails[session.csv_path] = [];
    }
    if (_selCsv === session.csv_path) renderRight();
  }

  // ── Meta enrichment (track, laps, best) ──────────────────────────────────────

  async function enrichMeta(sessions) {
    // Queue sessions that don't have meta yet
    for (const s of sessions) {
      if (!_meta[s.csv_path]) _metaQueue.push(s);
    }
    if (_metaBusy) return;
    _metaBusy = true;

    while (_metaQueue.length > 0) {
      const batch = _metaQueue.splice(0, 6);
      await Promise.all(batch.map(async s => {
        try {
          const m = await API.getSessionMeta(s.csv_path);
          _meta[s.csv_path] = m;
          // Also mirror sync_offset from config
          if (_config?.offsets?.[s.csv_path] != null)
            s.sync_offset = _config.offsets[s.csv_path];
        } catch (_) {}
      }));
      recomputeDayBest();
      renderLeft();
    }
    _metaBusy = false;
  }

  // ── Scan ──────────────────────────────────────────────────────────────────────

  function setStatus(msg) {
    _statusMsg = msg;
    const el = _container?.querySelector('#scan-status');
    if (el) el.textContent = msg;
  }

  async function doScan(auto = false) {
    if (_scanning) return;
    _config = _config || await API.getConfig();
    const paths = _config?.all_telemetry_paths || [];
    if (!paths.length) {
      setStatus('No telemetry folders configured — go to Settings first.');
      return;
    }

    _scanning = true;
    setStatus(auto ? 'Auto-scanning…' : 'Scanning…');
    _container?.querySelector('#scan-btn')?.setAttribute('disabled', '');

    try {
      const all = [];
      for (const p of paths) {
        const r = await API.scanSessions(p);
        all.push(...r);
      }
      _sessions = all;
      // Apply stored offsets
      for (const s of _sessions) {
        if (_config?.offsets?.[s.csv_path] != null)
          s.sync_offset = _config.offsets[s.csv_path];
      }
      _metaQueue = []; // reset queue so new sessions get fetched
      renderLeft();
      setStatus(`${_sessions.length} session${_sessions.length!==1?'s':''} found.`);
      enrichMeta(_sessions);
    } catch (e) {
      setStatus('Scan failed: ' + e);
    }

    _scanning = false;
    _container?.querySelector('#scan-btn')?.removeAttribute('disabled');
  }

  // ── Mount / Unmount ────────────────────────────────────────────────────────────

  async function mount(container) {
    _container = container;

    container.innerHTML = `
<div class="page data-page">
  <div class="toolbar">
    <div class="toolbar-left">
      <span class="page-title">Data</span>
      <span class="status-text" id="scan-status">${esc(_statusMsg||'Loading…')}</span>
    </div>
    <div class="toolbar-right">
      <button class="btn btn-secondary" id="scan-btn">↺ Scan</button>
    </div>
  </div>
  <div class="page-divider"></div>

  <div class="data-split">

    <!-- Left: session list -->
    <div class="data-left-panel">
      <div class="dl-header">
        <span class="dl-col dl-col-icon"></span>
        <span class="dl-col dl-col-time">Time</span>
        <span class="dl-col dl-col-track">Track</span>
        <span class="dl-col dl-col-src">Source</span>
        <span class="dl-col dl-col-num">Laps</span>
        <span class="dl-col dl-col-num">Best</span>
      </div>
      <div class="dl-scroll" id="data-left"></div>
    </div>

    <!-- Right: detail + sync -->
    <div class="data-right-panel" id="data-right">
      <div class="dr-empty">Select a session to see details and align video.</div>
    </div>

  </div>

  <div class="data-footer" id="data-footer">
    <span class="footer-hint">Select a session, pick laps, click <strong>+ Add to Export</strong></span>
  </div>
</div>`;

    container.querySelector('#scan-btn').addEventListener('click', () => doScan(false));

    // If we already have sessions from this session's scan, restore immediately
    if (_sessions.length > 0) {
      renderLeft();
      if (_selCsv) renderRight();
      refreshFooter();
      setStatus(_statusMsg);
      return;
    }

    // First visit: load config + cache, then auto-scan
    _config = await API.getConfig();

    // Apply stored offsets to sessions immediately
    const applyOffsets = () => {
      for (const s of _sessions) {
        if (_config?.offsets?.[s.csv_path] != null)
          s.sync_offset = _config.offsets[s.csv_path];
      }
    };

    try {
      const cached = await API.scanSessions('__cache__');
      if (cached && cached.length > 0) {
        _sessions = cached;
        applyOffsets();
        renderLeft();
        setStatus(`${_sessions.length} cached sessions — rescanning in background…`);
        enrichMeta(_sessions);
        // Auto-scan in background
        setTimeout(() => doScan(true), 200);
        return;
      }
    } catch (_) {}

    // No cache: auto-scan immediately
    setStatus('Scanning…');
    doScan(true);
  }

  function unmount() {
    _container = null;
    // Module state preserved intentionally
  }

  Router.register('data', { mount, unmount });
})();
