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
  let _selCsv     = null; // currently selected session csv_path
  let _config     = null;
  let _scanning    = false;
  let _autoSyncing = false;
  let _statusMsg   = '';
  let _container   = null;
  let _metaQueue   = [];   // sessions waiting for meta fetch
  let _metaBusy    = false;
  let _videoPort   = 0;    // localhost port of the Python video file server
  let _unlistenFns = [];   // push-event unlisten callbacks

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

  function videoUrl(winPath) {
    // Pass path as a query param so browser slash-normalization never mangles UNC paths
    if (_videoPort) return `http://127.0.0.1:${_videoPort}/?f=${encodeURIComponent(winPath)}`;
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
    const trackOverride = _config?.session_info?.[s.csv_path]?.info_track;
    const track   = trackOverride || m.track || baseName(s.csv_path);
    const lapStr  = m.laps  || '—';
    const bestStr = m.best  || (m.best_secs != null ? fmtTime(m.best_secs) : '—');
    const syncLabel = s.needs_conversion           ? '↻ conv'
                    : (!s.matched)                  ? 'no vid'
                    : s.sync_offset != null && s.sync_source === 'auto' ? '~ auto'
                    : s.sync_offset != null         ? '✓ user'
                    : '≈ unset';
    const iconCls   = s.needs_conversion           ? 'di-pending'
                    : (!s.matched)                  ? 'di-novid'
                    : s.sync_offset != null && s.sync_source === 'auto' ? 'di-auto'
                    : s.sync_offset != null         ? 'di-user'
                    : 'di-unsync';

    return `<div class="dl-row${isSel?' sel':''}${isDayB?' day-best':''}" data-csv="${esc(s.csv_path)}">
      <span class="dl-sync ${iconCls}">${syncLabel}</span>
      <span class="dl-time">${esc(time)}</span>
      <span class="dl-track" title="${esc(s.csv_path)}">${esc(track)}</span>
      <span class="dl-source">${esc(s.source||'RaceBox')}</span>
      <span class="dl-num">${esc(lapStr)}</span>
      <span class="dl-num">${esc(bestStr)}</span>
    </div>`;
  }

  function selectSession(csvPath) {
    _selCsv = csvPath;
    _container?.querySelectorAll('.dl-row').forEach(r => {
      r.classList.toggle('sel', r.dataset.csv === csvPath);
    });
    renderRight();

    const s = _sessions.find(x => x.csv_path === csvPath);
    if (!s) return;

    // Always keep previewSession in sync with the selected session
    // so navigating to Overlay via the sidebar also works
    State.set('previewSession', {
      csv_path:    s.csv_path,
      lap_idx:     0,
      video_paths: s.video_paths || [],
      sync_offset: s.sync_offset ?? 0,
      source:      s.source || 'RaceBox',
      csv_start:   s.csv_start  || null,
    });

    if (!_lapDetails[csvPath]) loadLaps(s);
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

    const m   = _meta[s.csv_path] || {};
    const off = s.sync_offset;

    // Session info
    const vidPaths    = s.video_paths || [];
    const hasVid      = s.matched && vidPaths.length > 0;
    const trackOverride  = _config?.session_info?.[s.csv_path]?.info_track;
    const effectiveTrack = trackOverride || m.track || '';

    pane.innerHTML = `
<!-- Info card -->
<div class="dr-card">
  <div class="dr-card-title">SESSION INFO</div>
  <div class="dr-rows">
    <div class="dr-row"><span class="dr-lbl">Source</span><span class="dr-val">${esc(s.source||'RaceBox')}</span></div>
    <div class="dr-row">
      <span class="dr-lbl">Track</span>
      <span class="dr-val dr-track-val" id="dr-track-display">
        ${esc(effectiveTrack||'—')}
        <button class="btn-inline-edit" id="dr-track-edit-btn" title="Edit track name" style="margin-left:4px;opacity:0.6;font-size:9px;cursor:pointer;border:none;background:none;color:var(--acc2);padding:0 2px">✎</button>
      </span>
    </div>
    <div class="dr-row"><span class="dr-lbl">Date</span><span class="dr-val">${esc(fmtDateTime(s.csv_start))}</span></div>
    <div class="dr-row"><span class="dr-lbl">Laps</span><span class="dr-val">${esc(m.laps||'—')}</span></div>
    <div class="dr-row"><span class="dr-lbl">Best</span><span class="dr-val" style="color:var(--ok)">${esc(m.best||'—')}</span></div>
    <div class="dr-row"><span class="dr-lbl">Video</span><span class="dr-val ${hasVid?'':'dr-warn'}">${hasVid ? `✓ ${vidPaths.length} clip(s)` : '✗ No match'}</span></div>
    <div class="dr-row"><span class="dr-lbl">Offset</span><span class="dr-val ${off!=null?'':'dr-warn'}" id="dr-off-display">${
      off!=null
        ? (s.sync_source === 'auto'
            ? `${off.toFixed(3)}s ~ auto`
            : `${off.toFixed(3)}s ✓`)
        : 'not set'
    }</span></div>
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

<!-- AIM conversion -->
${s.needs_conversion ? `
<div class="dr-card" id="dr-conv-card">
  <div class="dr-card-title">AIM XRK CONVERSION</div>
  <div class="dr-hint">This session must be converted from XRK format before it can be used.</div>
  <div class="dr-actions" style="margin-top:8px">
    <button class="btn btn-secondary btn-sm" id="dr-conv-btn">Convert to CSV</button>
    <span id="dr-conv-msg" class="status-msg"></span>
  </div>
</div>` : ''}

<!-- Video align -->
${hasVid ? renderAlignCard(s, vidPaths, off) : `
<div class="dr-card">
  <div class="dr-card-title">VIDEO</div>
  <div class="dr-hint" style="color:var(--warn)">No matching video found.</div>
  <div class="dr-actions" style="margin-top:8px">
    <button class="btn btn-secondary btn-sm" id="dr-assign-vid-btn">Browse for video…</button>
    <span id="dr-assign-vid-msg" class="status-msg"></span>
  </div>
</div>`}

<!-- Primary CTA: Open in Overlay -->
<button class="btn btn-accent" id="dr-goto-overlay"
        style="width:100%; padding:9px; font-size:11px; font-weight:600; border-radius:var(--radius); flex-shrink:0; margin-top:auto;">
  Open in Overlay →
</button>
`;

    wirePropPanel(s, pane);
  }

  function renderAlignCard(s, vidPaths, off) {
    const offVal    = off != null ? off.toFixed(3) : '';
    const isAuto    = s.sync_source === 'auto';
    const isSyncing = _autoSyncing && s.sync_offset == null && !s.auto_sync_failed;
    const autoNote  = isSyncing
      ? `<div style="font-size:9px;color:#ffb74d;margin-bottom:6px;padding:5px 6px;
                     background:rgba(255,183,77,0.08);border-radius:4px;border-left:2px solid #ffb74d">
           Auto-detecting sync offset… scrub will be available once complete.
         </div>`
      : isAuto
      ? `<div style="font-size:9px;color:#64b5f6;margin-bottom:6px;padding:5px 6px;
                     background:rgba(100,181,246,0.08);border-radius:4px;border-left:2px solid #64b5f6">
           Auto-detected: ${off != null ? off.toFixed(3)+'s' : '—'} — scrub to verify, then click Mark to confirm
         </div>`
      : '';
    return `
<div class="dr-card dr-align-card">
  <div class="dr-card-title">ALIGN VIDEO</div>
  ${autoNote}
  <video id="sync-video" class="sync-video" preload="metadata"
         src="${esc(videoUrl(vidPaths[0]))}"></video>
  <div class="sync-controls">
    <button class="btn btn-sm" id="sv-mm">◀◀ −1s</button>
    <button class="btn btn-sm" id="sv-m">◀ −1f</button>
    <button class="btn btn-sm" id="sv-p">▶ +1f</button>
    <button class="btn btn-sm" id="sv-pp">▶▶ +1s</button>
    <span class="sync-time" id="sv-time">0:00.000</span>
  </div>
  <input type="range" id="sv-scrub" class="sync-scrub" min="0" max="1000" value="0" step="1">
  <div class="sync-mark-row">
    <button class="btn btn-ok btn-sm" id="sv-mark">${isAuto ? '✓ Confirm Lap 1 start' : '🏁 Mark Lap 1 start'}</button>
    <input type="number" id="sv-off-input" class="input-field input-narrow sync-off-input"
           step="0.001" value="${esc(offVal)}" placeholder="0.000" title="Current offset (s) — follows video position">
    <span class="sync-mark-val" id="sv-mark-val">${off!=null && !isAuto ? '✓ saved' : ''}</span>
  </div>
  ${vidPaths.length > 1 ? `<div style="font-size:9px;color:var(--text3);margin-top:4px">+ ${vidPaths.length-1} more clip(s)</div>` : ''}
</div>`;
  }

  function _showBulkRenamePrompt(pane, oldName, newName, count) {
    // Remove any existing prompt first
    pane.querySelector('#bulk-rename-banner')?.remove();

    const banner = document.createElement('div');
    banner.id = 'bulk-rename-banner';
    banner.style.cssText = `
      margin:8px 0 0; padding:8px 10px; border-radius:4px;
      background:rgba(99,102,241,0.12); border:1px solid rgba(99,102,241,0.35);
      font-size:10px; color:var(--text1); line-height:1.5;
    `;
    banner.innerHTML = `
      <div style="margin-bottom:6px">
        <strong>${count} other session${count !== 1 ? 's' : ''}</strong>
        also named <em>"${esc(oldName)}"</em>.<br>
        Rename all of them to <em>"${esc(newName)}"</em> as well?
      </div>
      <div style="display:flex;gap:6px">
        <button class="btn btn-sm btn-accent" id="bulk-rename-yes">Yes, rename all</button>
        <button class="btn btn-sm" id="bulk-rename-no">No, just this one</button>
      </div>
    `;

    // Insert after the info card (first .dr-card)
    const firstCard = pane.querySelector('.dr-card');
    firstCard ? firstCard.after(banner) : pane.prepend(banner);

    banner.querySelector('#bulk-rename-yes').addEventListener('click', async () => {
      banner.innerHTML = '<span style="color:var(--text3)">Renaming…</span>';
      try {
        // Compute the exact paths to rename here in JS where _meta is available,
        // rather than letting the backend guess from the (often empty) scan cache.
        if (!_config.session_info) _config.session_info = {};
        const pathsToRename = [];
        for (const sess of _sessions) {
          if (sess.csv_path === _selCsv) continue;
          const ov = _config.session_info[sess.csv_path]?.info_track;
          const mt = _meta[sess.csv_path]?.track;
          if ((ov || mt || '').trim().toLowerCase() === oldName.toLowerCase()) {
            pathsToRename.push(sess.csv_path);
          }
        }
        await API.bulkRenameTrack(pathsToRename, newName);
        // Apply to local config cache
        for (const csvPath of pathsToRename) {
          const ex = _config.session_info[csvPath] || {};
          _config.session_info[csvPath] = { ...ex, info_track: newName };
        }
        banner.remove();
        renderLeft();
      } catch (e) {
        banner.innerHTML = `<span style="color:var(--err)">Error: ${esc(String(e))}</span>`;
      }
    });

    banner.querySelector('#bulk-rename-no').addEventListener('click', () => banner.remove());
  }

  function wirePropPanel(s, pane) {
    // Inline track name edit
    pane.querySelector('#dr-track-edit-btn')?.addEventListener('click', () => {
      const display     = pane.querySelector('#dr-track-display');
      const trackOverride  = _config?.session_info?.[s.csv_path]?.info_track;
      const currentTrack   = trackOverride || _meta[s.csv_path]?.track || '';
      display.innerHTML = `
        <input type="text" class="input-field" id="dr-track-input"
               value="${esc(currentTrack)}" style="width:130px;font-size:11px;padding:1px 4px">
        <button class="btn btn-sm" id="dr-track-save" style="padding:1px 5px;margin-left:2px">✓</button>
        <button class="btn btn-sm" id="dr-track-cancel" style="padding:1px 5px">✕</button>
      `;
      const inp = pane.querySelector('#dr-track-input');
      inp?.focus(); inp?.select();

      const saveTrack = async () => {
        const newTrack = pane.querySelector('#dr-track-input')?.value?.trim() ?? '';
        if (newTrack === currentTrack) { renderRight(); return; }

        // Save this session first
        const existing = _config?.session_info?.[s.csv_path] || {};
        await API.editSessionInfo(s.csv_path, { ...existing, info_track: newTrack });
        if (!_config.session_info) _config.session_info = {};
        _config.session_info[s.csv_path] = { ...existing, info_track: newTrack };

        // Count other sessions sharing the old effective track name
        const others = currentTrack ? _sessions.filter(sess => {
          if (sess.csv_path === s.csv_path) return false;
          const ov = _config?.session_info?.[sess.csv_path]?.info_track;
          const mt = _meta[sess.csv_path]?.track;
          return (ov || mt || '').trim().toLowerCase() === currentTrack.toLowerCase();
        }) : [];

        renderRight();
        renderLeft();

        if (others.length > 0) {
          _showBulkRenamePrompt(pane, currentTrack, newTrack, others.length);
        }
      };

      pane.querySelector('#dr-track-save')?.addEventListener('click', saveTrack);
      pane.querySelector('#dr-track-cancel')?.addEventListener('click', () => renderRight());
      inp?.addEventListener('keydown', e => {
        if (e.key === 'Enter')  saveTrack();
        if (e.key === 'Escape') renderRight();
      });
    });

    // Open in Overlay
    pane.querySelector('#dr-goto-overlay')?.addEventListener('click', () => {
      const laps = _lapDetails[s.csv_path] || [];
      const lap  = laps.find(l => l.is_best) || laps[0];
      State.set('previewSession', {
        csv_path:    s.csv_path,
        lap_idx:     lap ? lap.lap_idx : 0,
        video_paths: s.video_paths || [],
        sync_offset: s.sync_offset ?? 0,
        source:      s.source || 'RaceBox',
        csv_start:   s.csv_start  || null,
      });
      Router.navigate('editor');
    });

    // AIM XRK conversion
    pane.querySelector('#dr-conv-btn')?.addEventListener('click', async () => {
      const btn = pane.querySelector('#dr-conv-btn');
      const msg = pane.querySelector('#dr-conv-msg');
      btn.disabled = true;
      if (msg) { msg.textContent = 'Converting…'; msg.className = 'status-msg status-dim'; }
      try {
        const result = await API.convertXrkSession(s.csv_path);
        if (result && result.ok) {
          if (msg) { msg.textContent = 'Done — re-scanning…'; msg.className = 'status-msg status-ok'; }
          s.needs_conversion = false;
          await doScan(true);
        } else {
          if (msg) { msg.textContent = result?.error || 'Conversion failed.'; msg.className = 'status-msg status-err'; }
          btn.disabled = false;
        }
      } catch (e) {
        if (msg) { msg.textContent = String(e); msg.className = 'status-msg status-err'; }
        btn.disabled = false;
      }
    });

    // Manual video assignment
    pane.querySelector('#dr-assign-vid-btn')?.addEventListener('click', async () => {
      const btn = pane.querySelector('#dr-assign-vid-btn');
      const msg = pane.querySelector('#dr-assign-vid-msg');
      btn.disabled = true;
      const videoPath = await API.openFileDialog(['Video Files (*.mp4;*.mov;*.avi;*.mkv;*.MP4;*.MOV)']).catch(() => null);
      if (!videoPath) { btn.disabled = false; return; }
      try {
        await API.assignVideo(s.csv_path, videoPath);
        s.video_paths = [videoPath];
        s.matched     = true;
        // Update previewSession so editor picks up the new video immediately
        const prev = State.get('previewSession');
        if (prev?.csv_path === s.csv_path) {
          State.set('previewSession', { ...prev, video_paths: s.video_paths, sync_offset: s.sync_offset ?? 0 });
        }
        await API.saveSessionsCache(_sessions).catch(() => {});
        renderRight();
        renderLeft();
        // Trigger auto-sync for this session now that it has a video
        if (_config?.auto_sync_enabled && s.sync_offset == null && !s.auto_sync_failed) {
          API.startAutoSync([s]).then(r => {
            if (r?.queued > 0) {
              _autoSyncing = true;
              setStatus(`Video assigned — auto-syncing…`);
              if (_selCsv === s.csv_path) renderRight();
            }
          }).catch(() => {});
        }
      } catch (e) {
        if (msg) { msg.textContent = String(e); msg.className = 'status-msg status-err'; }
        btn.disabled = false;
      }
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

  function wireVideoSync(s, pane) {
    const video  = pane.querySelector('#sync-video');
    const scrub  = pane.querySelector('#sv-scrub');
    const timeEl = pane.querySelector('#sv-time');
    const markEl = pane.querySelector('#sv-mark-val');
    const offInp = pane.querySelector('#sv-off-input');

    if (!video) return;

    let fps = 30; // default; will be updated from metadata

    // outlapDur: elapsed_start of the first timed lap (already cached from loadLaps).
    // sync_offset = (video time at lap-1 mark) - outlapDur, matching video_renderer.py
    // semantics where sync_offset = video time at session start.
    function getOutlapDur() {
      const laps = _lapDetails[s.csv_path] || [];
      const firstTimed = laps.find(l => !l.is_outlap);
      return (firstTimed?.elapsed_start) || 0;
    }

    function fmtVTime(t) {
      const m = Math.floor(t / 60);
      const sec = (t % 60).toFixed(3).padStart(6, '0');
      return `${m}:${sec}`;
    }

    let _sought = false; // guard: seek once per wireVideoSync call

    function seekToLap1() {
      if (s.sync_offset == null || !video.duration) return;
      const outlapDur = getOutlapDur();
      const lap1Vid = Math.max(0, Math.min(video.duration, s.sync_offset + outlapDur));
      _sought = true;
      if (scrub) scrub.max = Math.round(video.duration * 1000);
      video.currentTime = lap1Vid;
      if (scrub) scrub.value = Math.round(lap1Vid * 1000);
      if (timeEl) timeEl.textContent = fmtVTime(lap1Vid);
      if (offInp) offInp.value = lap1Vid.toFixed(3);
    }

    video.addEventListener('loadedmetadata', () => {
      scrub.max = Math.round(video.duration * 1000);
      fps = 30;
      if (!_sought) seekToLap1();
    });

    // Fallback: canplay fires later than loadedmetadata and is more reliable in some WebView builds
    video.addEventListener('canplay', () => { if (!_sought) seekToLap1(); }, { once: true });

    video.addEventListener('timeupdate', () => {
      if (!video.seeking) {
        scrub.value = Math.round(video.currentTime * 1000);
        if (timeEl) timeEl.textContent = fmtVTime(video.currentTime);
        if (offInp) offInp.value = video.currentTime.toFixed(3);
      }
    });

    scrub?.addEventListener('input', () => {
      _sought = true; // user is scrubbing; suppress any delayed auto-seek
      video.currentTime = scrub.value / 1000;
      if (timeEl) timeEl.textContent = fmtVTime(video.currentTime);
      if (offInp) offInp.value = (scrub.value / 1000).toFixed(3);
    });

    function step(frameDelta) {
      const dt = Math.abs(frameDelta) > 10 ? (frameDelta > 0 ? 1 : -1) : frameDelta / fps;
      video.currentTime = Math.max(0, Math.min(video.duration || 0, video.currentTime + dt));
    }

    pane.querySelector('#sv-mm')?.addEventListener('click', () => step(-fps));
    pane.querySelector('#sv-m')?.addEventListener ('click', () => step(-1));
    pane.querySelector('#sv-p')?.addEventListener ('click', () => step(1));
    pane.querySelector('#sv-pp')?.addEventListener('click', () => step(fps));

    // Mark: save (video.currentTime - outlapDur) as sync_offset.
    // vid_t = sync_offset + lap.elapsed_start works correctly for all laps.
    pane.querySelector('#sv-mark')?.addEventListener('click', async () => {
      const rawTime   = video.currentTime;
      const outlapDur = getOutlapDur();
      const offset    = rawTime - outlapDur;
      s.sync_offset = offset;
      s.sync_source = 'user';
      await saveOffset(s);
      renderLeft();
      renderRight(); // re-renders the panel so the auto banner and button label update
    });

    // If metadata already available (e.g. browser cache), seek immediately.
    if (video.readyState >= 1 && s.sync_offset != null) {
      seekToLap1();
    } else if (video.readyState < 1) {
      // Force load in case preload="metadata" was suppressed (WebView2 cache behaviour)
      video.load();
    }
  }

  // Called after renderRight() from the auto_sync_progress 'done' handler.
  // wireVideoSync already attaches a loadedmetadata/canplay listener, but if the
  // browser returns readyState >= 1 immediately (cached metadata), those events
  // never fire and the existing readyState check inside wireVideoSync might have
  // run before video.load() finished.  This function does one final check and
  // seeks if the element is now ready.
  function _seekVideoAfterAutoSync(s, syncOffset) {
    const pane = _container?.querySelector('#data-right');
    const vid  = pane?.querySelector('#sync-video');
    if (!vid || vid.readyState < 1 || !vid.duration) return;
    const laps       = _lapDetails[s.csv_path] || [];
    const firstTimed = laps.find(l => !l.is_outlap);
    const outlapDur  = firstTimed?.elapsed_start || 0;
    const lap1Vid    = Math.max(0, Math.min(vid.duration, syncOffset + outlapDur));
    const scrub  = pane.querySelector('#sv-scrub');
    const timeEl = pane.querySelector('#sv-time');
    const offInp = pane.querySelector('#sv-off-input');
    if (scrub) scrub.max = Math.round(vid.duration * 1000);
    vid.currentTime = lap1Vid;
    if (scrub) scrub.value = Math.round(lap1Vid * 1000);
    if (timeEl) timeEl.textContent = `${Math.floor(lap1Vid / 60)}:${(lap1Vid % 60).toFixed(3).padStart(6, '0')}`;
    if (offInp) offInp.value = lap1Vid.toFixed(3);
  }

  async function saveOffset(s) {
    const offsets        = { ...(_config?.offsets || {}),         [s.csv_path]: s.sync_offset };
    const offset_sources = { ...(_config?.offset_sources || {}),  [s.csv_path]: 'user' };
    _config = { ..._config, offsets, offset_sources };
    await API.saveConfig({ offsets, offset_sources });
    // Keep previewSession offset in sync
    const prev = State.get('previewSession');
    if (prev && prev.csv_path === s.csv_path) {
      State.set('previewSession', { ...prev, sync_offset: s.sync_offset });
    }
  }

  // ── Footer ────────────────────────────────────────────────────────────────────

  function refreshFooter() {
    const footer = _container?.querySelector('#data-footer');
    if (!footer) return;
    const hint = _sessions.length
      ? `${_sessions.length} session${_sessions.length !== 1 ? 's' : ''} — select one to get started.`
      : 'Select a session to get started.';
    footer.innerHTML = `<span class="footer-hint">${esc(hint)}</span>`;
  }

  // ── Lap loading ───────────────────────────────────────────────────────────────

  async function loadLaps(session) {
    if (_lapDetails[session.csv_path]) { renderRight(); return; }
    try {
      const laps = await API.getLaps(session.csv_path);
      _lapDetails[session.csv_path] = laps;
    } catch (err) {
      console.warn('getLaps failed for', session.csv_path, err);
      _lapDetails[session.csv_path] = [];
    }
    if (_selCsv === session.csv_path) {
      renderRight();
      // Update previewSession with best lap index now that we know it
      const laps  = _lapDetails[session.csv_path] || [];
      const best  = laps.find(l => l.is_best) || laps[0];
      const prev  = State.get('previewSession');
      if (prev && prev.csv_path === session.csv_path && best) {
        State.set('previewSession', { ...prev, lap_idx: best.lap_idx });
      }
    }
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
          // Write track back into the session object so it persists in the cache
          if (m.track) s.track = m.track;
          // Mirror sync fields from config in case they updated since last scan
          if (_config?.offsets?.[s.csv_path] != null) {
            s.sync_offset = _config.offsets[s.csv_path];
            s.sync_source = _config.offset_sources?.[s.csv_path] ?? s.sync_source;
          }
        } catch (err) {
          console.warn('getSessionMeta failed for', s.csv_path, err);
        }
      }));
      recomputeDayBest();
      renderLeft();
    }
    _metaBusy = false;
    // Persist enriched track names to cache so backend queries (ref picker,
    // personal best, bulk rename) can match by track without loading every session.
    API.saveSessionsCache(_sessions).catch(() => {});
  }

  // ── Scan ──────────────────────────────────────────────────────────────────────

  function setStatus(msg) {
    _statusMsg = msg;
    const el = _container?.querySelector('#scan-status');
    if (el) el.textContent = msg;
    refreshFooter();
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
    _autoSyncing = false;
    setStatus(auto ? 'Auto-scanning…' : 'Scanning…');
    _container?.querySelector('#scan-btn')?.setAttribute('disabled', '');

    try {
      const all = [];
      for (const p of paths) {
        const r = await API.scanSessions(p);
        all.push(...r);
      }
      _sessions = all;
      // Apply stored offsets and sources
      for (const s of _sessions) {
        if (_config?.offsets?.[s.csv_path] != null) {
          s.sync_offset = _config.offsets[s.csv_path];
          s.sync_source = _config.offset_sources?.[s.csv_path] ?? s.sync_source;
        }
      }
      // Publish to global State so other pages (e.g. export) can access the list
      State.set('sessions', _sessions);
      _metaQueue = []; // reset queue so new sessions get fetched
      renderLeft();
      setStatus(`${_sessions.length} session${_sessions.length!==1?'s':''} found.`);
      enrichMeta(_sessions);
      // Persist the full merged list so next startup shows cached results immediately
      API.saveSessionsCache(_sessions).catch(() => {});
      // Trigger background auto-sync if enabled
      if (_config?.auto_sync_enabled) {
        const candidates = _sessions.filter(s => s.matched && s.video_paths?.length);
        API.startAutoSync(candidates).then(r => {
          if (r?.queued > 0) {
            _autoSyncing = true;
            setStatus(`${_sessions.length} sessions found — auto-syncing ${r.queued} session(s)…`);
          }
        }).catch(() => {});
      }
    } catch (e) {
      setStatus('Scan failed: ' + e);
    }

    _scanning = false;
    _container?.querySelector('#scan-btn')?.removeAttribute('disabled');
  }

  // ── Drag resizer ──────────────────────────────────────────────────────────────

  function initResizer(container) {
    const split     = container.querySelector('#data-split');
    const leftPanel = container.querySelector('#data-left-panel');
    const resizer   = container.querySelector('#data-resizer');
    if (!split || !leftPanel || !resizer) return;

    // Restore saved ratio (default 50%)
    const saved = parseFloat(localStorage.getItem('data-split-ratio') || '0.5');
    leftPanel.style.flexBasis = (saved * 100).toFixed(2) + '%';

    let dragging = false;

    resizer.addEventListener('mousedown', e => {
      dragging = true;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });

    const onMove = e => {
      if (!dragging) return;
      const rect  = split.getBoundingClientRect();
      let   ratio = (e.clientX - rect.left) / rect.width;
      ratio = Math.max(0.2, Math.min(0.8, ratio));
      leftPanel.style.flexBasis = (ratio * 100).toFixed(2) + '%';
    };

    const onUp = () => {
      if (!dragging) return;
      dragging = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      // Persist
      const rect      = split.getBoundingClientRect();
      const leftRect  = leftPanel.getBoundingClientRect();
      const ratio     = leftRect.width / rect.width;
      localStorage.setItem('data-split-ratio', ratio.toFixed(4));
    };

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup',   onUp);

    // Clean up on unmount (store removers on the container element)
    container._resizerCleanup = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup',   onUp);
    };
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

  <div class="data-split" id="data-split">

    <!-- Left: session list -->
    <div class="data-left-panel" id="data-left-panel">
      <div class="dl-header">
        <span class="dl-col dl-col-sync">Sync</span>
        <span class="dl-col dl-col-time">Time</span>
        <span class="dl-col dl-col-track">Track</span>
        <span class="dl-col dl-col-src">Source</span>
        <span class="dl-col dl-col-num">Laps</span>
        <span class="dl-col dl-col-num">Best</span>
      </div>
      <div class="dl-scroll" id="data-left"></div>
    </div>

    <!-- Drag resizer -->
    <div class="data-resizer" id="data-resizer"></div>

    <!-- Right: detail + sync -->
    <div class="data-right-panel" id="data-right">
      <div class="dr-empty">Select a session to see details and align video.</div>
    </div>

  </div>

  <div class="data-footer" id="data-footer">
    <span class="footer-hint">Select a session to get started.</span>
  </div>
</div>`;

    container.querySelector('#scan-btn').addEventListener('click', () => doScan(false));
    initResizer(container);

    // XRK auto-conversion progress from the backend
    _unlistenFns.push(API.on('scan_status', detail => {
      if (_scanning) setStatus(detail.message || '');
    }));

    // Listen to auto-sync push events to update session status in real-time
    let _asIdx = 0, _asTotal = 0, _asDone = 0, _asFailed = 0;
    _unlistenFns.push(API.on('auto_sync_progress', detail => {
      const s = _sessions.find(x => x.csv_path === detail.csv_path);
      if (!s) return;
      if (detail.status === 'processing') {
        _asIdx = detail.current; _asTotal = detail.total;
        setStatus(`Auto-syncing session ${_asIdx} of ${_asTotal}…`);
      } else if (detail.status === 'checking') {
        const conf = detail.confidence?.toFixed(2);
        const secs = detail.vid_t?.toFixed(0);
        setStatus(`Auto-syncing session ${_asIdx} of ${_asTotal} — ${secs}s of video decoded, confidence ${conf}× (need 6×)`);
      } else if (detail.status === 'done') {
        _asDone++;
        // Don't overwrite if the user already confirmed this session while we were processing
        if (s.sync_source !== 'user') {
          s.sync_offset = detail.offset;
          s.sync_source = 'auto';
          // Keep previewSession in sync so editor gets the detected offset
          const prev = State.get('previewSession');
          if (prev?.csv_path === s.csv_path) {
            State.set('previewSession', { ...prev, video_paths: s.video_paths || prev.video_paths, sync_offset: detail.offset });
          }
          renderLeft();
          renderRight();
          // If the video element is already loaded (WebView2 cache), wireVideoSync's
          // readyState check runs before load() triggers metadata — seek it now.
          _seekVideoAfterAutoSync(s, detail.offset);
        }
        const off = detail.offset >= 0 ? `+${detail.offset.toFixed(3)}s` : `${detail.offset.toFixed(3)}s`;
        setStatus(`Auto-sync: offset detected ${off} at ${detail.confidence?.toFixed(2)}× confidence`);
      } else if (detail.status === 'failed') {
        _asFailed++;
        s.auto_sync_failed = true;
        setStatus(`Auto-sync: no confident match (${detail.confidence?.toFixed(2)}× confidence) — set offset manually`);
        renderLeft();
        renderRight();
      }
    }));
    _unlistenFns.push(API.on('auto_sync_done', () => {
      _autoSyncing = false;
      const summary = _asDone > 0 || _asFailed > 0
        ? ` — ${_asDone} matched, ${_asFailed} skipped`
        : '';
      setStatus(`${_sessions.length} session${_sessions.length !== 1 ? 's' : ''} found. Auto-sync complete${summary}.`);
    }));

    // Await the video server port before rendering so videoUrl() is correct from the start
    if (!_videoPort) {
      _videoPort = await API.getVideoServerPort().catch(() => 0);
    }

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
        State.set('sessions', _sessions);
        renderLeft();
        setStatus(`${_sessions.length} cached sessions — rescanning in background…`);
        enrichMeta(_sessions);
        // Auto-scan in background
        setTimeout(() => doScan(true), 200);
        return;
      }
    } catch (err) {
      console.warn('Failed to load session cache:', err);
    }

    // No cache: auto-scan immediately
    setStatus('Scanning…');
    doScan(true);
  }

  function unmount() {
    if (_container?._resizerCleanup) _container._resizerCleanup();
    _unlistenFns.forEach(fn => fn());
    _unlistenFns = [];
    _container = null;
    // Module state (_sessions, _meta, etc.) preserved intentionally across navigations
  }

  Router.register('data', { mount, unmount });
})();
