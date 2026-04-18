/**
 * editor.js — Overlay Editor page.
 *
 * Architecture:
 *   - Left panel: gauge list + Add Gauge button
 *   - Right panel: 16:9 preview canvas with per-gauge sub-canvases
 *   - Gauges are positioned as absolutely-placed <canvas> elements
 *     on top of the preview area
 *   - Drag/resize handled via mouse events on a transparent overlay
 *   - Live Canvas renders using gauges/base.js + individual gauge modules
 */
(function () {
  // ── Registry: maps style name → render function ────────────────────────────
  const GAUGE_RENDERERS = {
    'Numeric':    (ctx, d, w, h) => GaugeNumeric.render(ctx, d, w, h),
    'Info':       (ctx, d, w, h) => GaugeInfo.render(ctx, d, w, h),
    'Scoreboard': (ctx, d, w, h) => GaugeScoreboard.render(ctx, d, w, h),
    'Bar':        (ctx, d, w, h) => GaugeBar.render(ctx, d, w, h),
    'Line':       (ctx, d, w, h) => GaugeLine.render(ctx, d, w, h),
    'Delta':      (ctx, d, w, h) => GaugeDelta.render(ctx, d, w, h),
    'Compare':    (ctx, d, w, h) => GaugeCompare.render(ctx, d, w, h),
    'Multi-Line': (ctx, d, w, h) => GaugeMultiline.render(ctx, d, w, h),
    'Splits':     (ctx, d, w, h) => GaugeSplits.render(ctx, d, w, h),
    'Sector Bar': (ctx, d, w, h) => GaugeSectorBar.render(ctx, d, w, h),
    'Dial':       (ctx, d, w, h) => GaugeDial.render(ctx, d, w, h),
    'G-Meter':    (ctx, d, w, h) => GaugeGmeter.render(ctx, d, w, h),
    'Lean':       (ctx, d, w, h) => GaugeLean.render(ctx, d, w, h),
    'Circuit':    (ctx, d, w, h) => GaugeMap.render(ctx, d, w, h),
    'Zoomed':     (ctx, d, w, h) => GaugeMap.renderZoomed(ctx, d, w, h),
    'Image':      (ctx, d, w, h) => GaugeImage.render(ctx, d, w, h),
  };

  // ── Channel → valid styles map (mirrors gauge_channels.py) ─────────────────
  const CHANNEL_STYLES = {
    speed:       ['Dial', 'Bar', 'Numeric', 'Line', 'Compare'],
    rpm:         ['Numeric', 'Bar', 'Dial', 'Line'],
    exhaust_temp:['Numeric', 'Bar', 'Line'],
    gforce_lon:  ['Bar', 'Dial', 'Numeric', 'Line', 'Compare'],
    gforce_lat:  ['Bar', 'Dial', 'Numeric', 'Line', 'Compare'],
    g_meter:     ['G-Meter'],
    lean:        ['Lean', 'Bar', 'Dial', 'Line', 'Numeric'],
    altitude:    ['Line', 'Bar', 'Numeric'],
    lap_time:    ['Numeric', 'Splits', 'Sector Bar', 'Line', 'Compare', 'Bar'],
    delta_time:  ['Delta', 'Numeric', 'Line', 'Compare'],
    map:         ['Circuit', 'Zoomed'],
    info:        ['Info'],
    lap_info:    ['Scoreboard'],
    multi:       ['Multi-Line'],
    image:       ['Image'],
  };

  const ALL_CHANNELS = [
    { value: 'speed',       label: 'Speed' },
    { value: 'rpm',         label: 'RPM' },
    { value: 'exhaust_temp',label: 'Exhaust Temp' },
    { value: 'gforce_lon',  label: 'Long G' },
    { value: 'gforce_lat',  label: 'Lat G' },
    { value: 'g_meter',     label: 'G-Meter' },
    { value: 'lean',        label: 'Lean Angle' },
    { value: 'altitude',    label: 'Altitude' },
    { value: 'lap_time',    label: 'Lap Time' },
    { value: 'delta_time',  label: 'Delta' },
    { value: 'map',         label: 'Map' },
    { value: 'info',        label: 'Session Info' },
    { value: 'lap_info',    label: 'Lap Info' },
    { value: 'multi',       label: 'Multi-Line' },
    { value: 'image',       label: 'Image / Logo' },
  ];

  // Channels that can appear inside a Multi-Line gauge
  const MULTI_CHANNEL_OPTS = [
    { value: 'speed',        label: 'Speed' },
    { value: 'rpm',          label: 'RPM' },
    { value: 'exhaust_temp', label: 'Exhaust Temp' },
    { value: 'gforce_lon',   label: 'Long G' },
    { value: 'gforce_lat',   label: 'Lat G' },
    { value: 'lean',         label: 'Lean Angle' },
    { value: 'altitude',     label: 'Altitude' },
    { value: 'lap_time',     label: 'Lap Time' },
    { value: 'delta_time',   label: 'Delta' },
  ];

  const GAUGE_COLOURS_LIST = [
    '#00d4ff','#ff6b35','#a8ff3e','#ff3ea8',
    '#ffd700','#3ea8ff','#ff3e3e','#3effd7',
    '#c084fc','#fb923c',
  ];

  // ── Utilities ─────────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── State ──────────────────────────────────────────────────────────────────
  let _layout    = null;   // {is_bike, theme, gauges:[...]}
  let _presets   = [];
  let _container = null;
  let _selected  = null;   // index of selected gauge
  let _drag      = null;   // {type:'move'|'resize', gaugeIdx, startMx, startMy, startG}
  let _animFrame  = null;

  // Live preview state
  let _liveSession     = null;  // {csv_path, lap_idx, video_paths, sync_offset, csv_start}
  let _liveSessionMeta = null;  // {track, laps, best, best_secs} from getSessionMeta
  let _liveLaps        = null;  // [{lap_idx, duration, is_best, elapsed_start}] from getLaps
  let _selLapIdx       = 0;     // currently previewed lap index
  let _livePoints      = null;  // [{t, speed, gx, gy, rpm, alt, lat, lon, lean}, ...]
  let _liveLats        = null;  // pre-extracted lat array for map gauge
  let _liveLons        = null;
  let _liveOffset      = 0;     // sync_offset: video_time - offset = lap_elapsed_time
  let _liveFrameIdx    = 0;
  let _livePort        = 0;
  let _liveRafId       = null;
  let _mountGen        = 0;     // incremented on unmount; guards stale async continuations
  let _resizeObserver  = null;

  // Constants (normalised)
  const MIN_NORM        = 0.04;
  const SNAP_NORM       = 0.02;   // edge-snap threshold
  const SNAP_ELEM_NORM  = 0.015;  // element-to-element snap threshold
  const SNAP_SIZE_STEP  = 0.05;   // size grid step for resize snap
  const HANDLE_NORM     = 0.012;  // resize handle size as fraction of preview width

  // ── Dummy data ─────────────────────────────────────────────────────────────
  function dummyData(channel, style, theme, gauge = null) {
    const base = { theme };
    switch (channel) {
      case 'info': {
        const ov = gauge?.info_overrides || {};
        return {
          ...base,
          info_track:   ov.track    || 'Spa-Francorchamps',
          info_date:    ov.date     || '2024-06-15',
          info_time:    ov.time     || '14:32',
          info_vehicle: ov.vehicle  || 'Porsche 992 GT3 R',
          info_session: ov.session  || 'Practice',
          info_weather: ov.weather  || '22°C  Partly cloudy',
          info_wind:    ov.wind     || 'NW  8 km/h',
          selected_fields: gauge?.selected_fields || ['track','datetime','vehicle','weather','wind'],
        };
      }
      case 'lap_info': return {
        ...base, lap_num: 3, total_laps: 8,
        lap_elapsed: 45.234, best_so_far: 83.456, delta_time: -0.234,
        selected_fields: gauge?.selected_fields || ['lap','best','current','delta'],
      };
      case 'image': return {
        ...base,
        image_path: gauge?.image_path || '',
        image_url:  '',   // no live URL in dummy mode — placeholder shown
        opacity:    gauge?.opacity ?? 1.0,
        fit:        gauge?.fit     || 'contain',
      };
      case 'map': return {
        ...base,
        lats: [], lons: [], cur_idx: 0,
        zoom_radius_m: gauge?.zoom_radius_m ?? 150,
        show_ref: gauge?.show_ref !== false,
        ref_lats: [], ref_lons: [],
      };
      case 'multi': {
        const mh = (amp, off) => Array.from({length:40}, (_,i)=>amp*Math.sin(i*0.25+off)+off);
        const keys = (gauge?.multi_channels && gauge.multi_channels.length)
          ? gauge.multi_channels
          : ['speed', 'gforce_lat'];
        const multi_channels = keys.map((ch, ci) => {
          const m = _LIVE_FIELDS[ch] || { label: ch, unit: '', min: 0, max: 100, sym: false, key: ch };
          const amp = (m.max - m.min) * 0.35;
          const off = (m.max + m.min) / 2;
          return {
            channel: ch, label: m.label, unit: m.unit,
            values: mh(amp, off), value: off + amp * 0.5,
            min_val: m.min, max_val: m.max, symmetric: m.sym, color_idx: ci,
          };
        });
        return { ...base, multi_channels };
      }
      default: {
        const meta = {
          speed:       {label:'Speed',     unit:'km/h', min:0,   max:250, sym:false, val:185},
          rpm:         {label:'RPM',       unit:'rpm',  min:0,   max:14000, sym:false, val:7200},
          exhaust_temp:{label:'Exh Temp',  unit:'°C',   min:0,   max:900, sym:false, val:650},
          gforce_lon:  {label:'Long G',    unit:'G',    min:-3,  max:3,   sym:true,  val:-1.2},
          gforce_lat:  {label:'Lat G',     unit:'G',    min:-3,  max:3,   sym:true,  val:2.1},
          g_meter:     {label:'G-Meter',   unit:'G',    min:-3,  max:3,   sym:true,  val:1.5},
          lean:        {label:'Lean',      unit:'°',    min:-60, max:60,  sym:true,  val:-35},
          altitude:    {label:'Altitude',  unit:'m',    min:0,   max:500, sym:false, val:220},
          lap_time:    {label:'Lap Time',  unit:'',     min:0,   max:120, sym:false, val:84.5},
          delta_time:  {label:'Delta',     unit:'s',    min:-30, max:30,  sym:true,  val:-0.234},
        }[channel] || {label:'Value', unit:'', min:0, max:100, sym:false, val:42};

        const hist = Array.from({length:40}, (_,i) => {
          const t = i * 0.1;
          return meta.min + (meta.max - meta.min) * (0.35 + 0.25 * Math.sin(t * 1.3) + 0.10 * Math.sin(t * 3.1));
        });
        const d = {
          ...base,
          value: meta.val, history_vals: hist, ref_history_vals: [],
          label: meta.label, unit: meta.unit,
          min_val: meta.min, max_val: meta.max,
          symmetric: meta.sym, channel,
          sectors: style === 'Splits' || style === 'Sector Bar' ? [
            {num:1, ref_t:24.5, cur_t:24.3, delta:-0.20, done:true, boundary_elapsed:24.3},
            {num:2, ref_t:23.1, cur_t:24.4, delta:1.30,  done:true, boundary_elapsed:48.7},
            {num:3, ref_t:25.8, cur_t:null, delta:null,  done:false, boundary_elapsed:Infinity},
          ] : [],
        };
        if (channel === 'g_meter') {
          d.value_gy = 0.8;
          d.history_gy = Array.from({length:40}, (_,i) => 1.5 * Math.cos(i * 0.25));
        }
        return d;
      }
    }
  }

  // ── Live data builder ──────────────────────────────────────────────────────
  const _LIVE_FIELDS = {
    speed:       { key:'speed',       label:'Speed',    unit:'km/h', min:0,   max:250,   sym:false },
    gforce_lon:  { key:'gx',          label:'Long G',   unit:'G',    min:-3,  max:3,     sym:true  },
    gforce_lat:  { key:'gy',          label:'Lat G',    unit:'G',    min:-3,  max:3,     sym:true  },
    rpm:         { key:'rpm',         label:'RPM',      unit:'rpm',  min:0,   max:14000, sym:false },
    exhaust_temp:{ key:'exhaust_temp',label:'Exh Temp', unit:'°C',   min:0,   max:900,   sym:false },
    altitude:    { key:'alt',         label:'Altitude', unit:'m',    min:0,   max:500,   sym:false },
    lean:        { key:'lean',        label:'Lean',     unit:'°',    min:-60, max:60,    sym:true  },
    lap_time:    { key:'t',           label:'Lap Time', unit:'',     min:0,   max:300,   sym:false },
  };

  function buildLiveData(channel, style, frameIdx, gauge = null) {
    const theme = _layout?.theme || 'Dark';
    const base  = { theme, channel };
    if (!_livePoints || !_livePoints.length) return dummyData(channel, style, theme, gauge);
    const idx = Math.max(0, Math.min(frameIdx, _livePoints.length - 1));
    const p   = _livePoints[idx];
    const histStart = Math.max(0, idx - 40);
    const hist = _livePoints.slice(histStart, idx + 1);

    if (channel === 'map') {
      return {
        theme,
        lats: _liveLats || [], lons: _liveLons || [], cur_idx: idx,
        zoom_radius_m: gauge?.zoom_radius_m ?? 150,
        show_ref: gauge?.show_ref !== false,
        ref_lats: [], ref_lons: [],
      };
    }
    if (channel === 'g_meter') {
      return {
        theme, channel,
        value:       p.gx  ?? 0,
        value_gy:    p.gy  ?? 0,
        history_vals:hist.map(pt => pt.gx ?? 0),
        history_gy:  hist.map(pt => pt.gy ?? 0),
        min_val: -3, max_val: 3, symmetric: true,
      };
    }
    if (channel === 'multi') {
      const keys = (gauge?.multi_channels && gauge.multi_channels.length)
        ? gauge.multi_channels : ['speed', 'gforce_lat'];
      const multi_channels = keys.map((ch, ci) => {
        const m = _LIVE_FIELDS[ch] || { label: ch, unit: '', min: 0, max: 100, sym: false, key: ch };
        return {
          channel: ch, label: m.label, unit: m.unit,
          values:    hist.map(pt => pt[m.key] ?? 0),
          value:     p[m.key] ?? 0,
          min_val:   m.min, max_val: m.max, symmetric: m.sym, color_idx: ci,
        };
      });
      return { theme, multi_channels };
    }
    if (channel === 'info') {
      const ov   = gauge?.info_overrides || {};
      const meta = _liveSessionMeta || {};
      let info_date = '', info_time = '';
      if (_liveSession?.csv_start) {
        try {
          const d = new Date(_liveSession.csv_start);
          info_date = d.toLocaleDateString();
          info_time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        } catch (_) {}
      }
      return {
        ...base,
        info_track:      ov.track    || meta.track || '',
        info_date:       ov.date     || info_date,
        info_time:       ov.time     || info_time,
        info_vehicle:    ov.vehicle  || '',
        info_session:    ov.session  || '',
        info_weather:    ov.weather  || '',
        info_wind:       ov.wind     || '',
        selected_fields: gauge?.selected_fields || ['track', 'datetime', 'vehicle', 'weather', 'wind'],
      };
    }
    if (channel === 'lap_info') {
      const laps      = _liveLaps || [];
      const timedLaps = laps.filter(l => !l.is_outlap && !l.is_inlap);
      const timedDurs = timedLaps.map(l => l.duration).filter(d => d != null);
      const best      = timedDurs.length ? Math.min(...timedDurs) : null;
      const idx2      = Math.max(0, Math.min(frameIdx, (_livePoints?.length || 1) - 1));
      const p2        = _livePoints?.[idx2];
      // Count only timed laps up to and including current selection
      const timedBefore = laps.slice(0, (_selLapIdx ?? 0) + 1)
                              .filter(l => !l.is_outlap && !l.is_inlap).length;
      return {
        ...base,
        lap_num:     timedBefore || 1,
        total_laps:  timedLaps.length || 1,
        lap_elapsed: p2?.t ?? 0,
        best_so_far: best,
        delta_time:  p2?.delta_time ?? null,
        selected_fields: gauge?.selected_fields || ['lap','best','current','delta'],
      };
    }
    if (channel === 'image') {
      const path = gauge?.image_path || '';
      const url  = (path && _livePort)
        ? `http://127.0.0.1:${_livePort}/?f=${encodeURIComponent(path)}`
        : '';
      return {
        ...base,
        image_path: path,
        image_url:  url,
        opacity:    gauge?.opacity ?? 1.0,
        fit:        gauge?.fit     || 'contain',
      };
    }
    const m = _LIVE_FIELDS[channel];
    if (!m) return dummyData(channel, style, theme, gauge);
    return {
      theme, channel,
      value:            p[m.key] ?? 0,
      history_vals:     hist.map(pt => pt[m.key] ?? 0),
      ref_history_vals: [],
      label: m.label, unit: m.unit,
      min_val: m.min, max_val: m.max, symmetric: m.sym,
      sectors: [],
    };
  }

  // ── Live preview helpers ────────────────────────────────────────────────────

  function _liveVideo() { return _container?.querySelector('#preview-video') || null; }

  function _findFrameIdx(telT) {
    const pts = _livePoints;
    if (!pts || !pts.length) return 0;
    if (telT <= pts[0].t) return 0;
    if (telT >= pts[pts.length - 1].t) return pts.length - 1;
    let lo = 0, hi = pts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (pts[mid].t < telT) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  function _rerenderLive() {
    const area = getPreviewEl();
    if (!area || !_layout) return;
    area.querySelectorAll('.gauge-canvas').forEach(el => {
      const idx = parseInt(el.dataset.gaugeIdx);
      if (!isNaN(idx) && _layout.gauges[idx]) renderGaugeEl(el, _layout.gauges[idx]);
    });
  }

  function _startLiveRaf() {
    if (_liveRafId) return;
    let lastIdx = -1;
    function tick() {
      const vid = _liveVideo();
      if (vid && _livePoints) {
        // sess_elapsed = vid_t - sync_offset  (mirrors video_renderer.py)
        // lap_elapsed  = sess_elapsed - lap.elapsed_start
        const lapStart = _liveLaps?.[_selLapIdx]?.elapsed_start ?? 0;
        const telT     = vid.currentTime - _liveOffset - lapStart;
        const newIdx   = _findFrameIdx(telT);
        if (newIdx !== lastIdx) {
          lastIdx = newIdx;
          _liveFrameIdx = newIdx;
          _rerenderLive();
        }
      }
      _liveRafId = requestAnimationFrame(tick);
    }
    _liveRafId = requestAnimationFrame(tick);
  }

  function _stopLiveRaf() {
    if (_liveRafId) { cancelAnimationFrame(_liveRafId); _liveRafId = null; }
  }

  // ── Lap time formatter ─────────────────────────────────────────────────────
  function _fmtLapTime(secs) {
    if (secs == null || isNaN(secs)) return '—';
    const m = Math.floor(secs / 60);
    const s = (secs % 60).toFixed(3).padStart(6, '0');
    return `${m}:${s}`;
  }

  // ── Update lap selector dropdown ────────────────────────────────────────────
  function _updateLapSelector() {
    const sel     = _container?.querySelector('#lap-sel');
    const prevBtn = _container?.querySelector('#lap-prev');
    const nextBtn = _container?.querySelector('#lap-next');
    if (!sel) return;

    const laps = _liveLaps || [];
    if (!laps.length) {
      sel.innerHTML = '<option value="">— no laps —</option>';
      if (prevBtn) prevBtn.disabled = true;
      if (nextBtn) nextBtn.disabled = true;
      return;
    }

    let timedCount = 0;
    sel.innerHTML = laps.map((l, i) => {
      const dur  = l.duration != null ? _fmtLapTime(l.duration) : '?';
      const star = l.is_best ? ' ★' : '';
      let label;
      if (l.is_outlap) {
        label = 'Outlap';
      } else if (l.is_inlap) {
        label = 'Inlap';
      } else {
        timedCount++;
        label = `Lap ${timedCount}`;
      }
      return `<option value="${i}" ${i === _selLapIdx ? 'selected' : ''}>${label}  ${dur}${star}</option>`;
    }).join('');

    if (prevBtn) prevBtn.disabled = (_selLapIdx <= 0);
    if (nextBtn) nextBtn.disabled = (_selLapIdx >= laps.length - 1);
  }

  // ── Wire video / scrub controls (called once from mount) ────────────────────
  function _wireVideoControls() {
    const vid     = _liveVideo();
    const scrub   = _container?.querySelector('#live-scrub');
    const timeEl  = _container?.querySelector('#live-time');
    const playBtn = _container?.querySelector('#live-play');

    if (vid) {
      // Apply the actual video aspect ratio to the preview area and persist it in
      // State so the next mount can use it immediately (avoiding the 16/9 flash).
      function _applyAspect() {
        if (!vid.videoWidth || !vid.videoHeight) return;
        const area = getPreviewEl();
        if (area) {
          area.style.aspectRatio = `${vid.videoWidth} / ${vid.videoHeight}`;
          requestAnimationFrame(() => rebuildGaugeCanvases());
        }
        // Persist so mount() can render the correct ratio before loadedmetadata fires.
        const ps = State.get('previewSession');
        if (ps && (ps.video_w !== vid.videoWidth || ps.video_h !== vid.videoHeight)) {
          State.set('previewSession', { ...ps, video_w: vid.videoWidth, video_h: vid.videoHeight });
        }
      }

      vid.addEventListener('loadedmetadata', () => {
        if (scrub) scrub.max = Math.round(vid.duration * 1000);
        _applyAspect();
        // Seek to the currently selected lap's start position.
        // (vid_t = sync_offset + lap.elapsed_start)
        if (_liveLaps?.[_selLapIdx] != null) {
          const lap = _liveLaps[_selLapIdx];
          const seekTo = _liveOffset + (lap.elapsed_start || 0);
          vid.currentTime = Math.max(0, Math.min(vid.duration, seekTo));
          if (scrub) scrub.value = Math.round(vid.currentTime * 1000);
        }
      });

      // Metadata may already be available if the browser cached it from a previous
      // load — in that case loadedmetadata won't fire again, so apply immediately.
      if (vid.readyState >= 1) _applyAspect();
      vid.addEventListener('timeupdate', () => {
        if (scrub && !vid.seeking) scrub.value = Math.round(vid.currentTime * 1000);
        if (timeEl) timeEl.textContent = _fmtVTime(vid.currentTime);
      });
      vid.addEventListener('play',  () => { if (playBtn) playBtn.textContent = '⏸'; });
      vid.addEventListener('pause', () => { if (playBtn) playBtn.textContent = '▶'; });
    }

    playBtn?.addEventListener('click', () => {
      if (vid) vid.paused ? vid.play() : vid.pause();
    });

    scrub?.addEventListener('input', e => {
      const v = _liveVideo();
      if (v && v.readyState >= 1 && v.duration) {
        // Video present — seek it; RAF syncs telemetry frame
        v.currentTime = parseFloat(e.target.value) / 1000;
      } else {
        // No video — drive telemetry directly from scrub position
        const maxMs = parseFloat(scrub.max) || 1000;
        const maxT  = _livePoints?.length ? _livePoints[_livePoints.length - 1].t : 1;
        const t     = (parseFloat(e.target.value) / maxMs) * maxT;
        _liveFrameIdx = _findFrameIdx(t);
        if (timeEl) timeEl.textContent = _fmtVTime(t);
        _rerenderLive();
      }
    });
  }

  // ── Load telemetry data for one lap ────────────────────────────────────────
  async function _loadLapData(lapIdx, mountGen) {
    if (!_liveSession) return;
    const labelEl = _container?.querySelector('#live-label');
    const scrub   = _container?.querySelector('#live-scrub');

    _livePoints   = null;
    _liveLats     = null;
    _liveLons     = null;
    _liveFrameIdx = 0;
    if (scrub) scrub.value = 0;

    try {
      const pts = await API.loadLapHistory(_liveSession.csv_path, lapIdx);
      if (mountGen !== undefined && _mountGen !== mountGen) return;  // stale

      _livePoints = pts;
      _liveLats   = pts.map(p => p.lat);
      _liveLons   = pts.map(p => p.lon);

      if (labelEl) labelEl.textContent = `${pts.length} samples · lap ${lapIdx + 1}`;

      // If no video, set scrub range from telemetry time span
      const vid = _liveVideo();
      if ((!vid || !vid.duration) && scrub && pts.length) {
        scrub.max = Math.round(pts[pts.length - 1].t * 1000);
      }

      // Immediately render frame 0 so gauges show real data without waiting for RAF
      _rerenderLive();
    } catch (e) {
      console.error('[_loadLapData] failed for lap', lapIdx, e);
      if (labelEl) labelEl.textContent = 'Telemetry load failed';
    }
  }

  // ── Switch lap (called from lap selector) ──────────────────────────────────
  async function switchLap(lapIdx) {
    if (!_liveSession || !_liveLaps) return;
    if (lapIdx < 0 || lapIdx >= _liveLaps.length) return;

    _selLapIdx = lapIdx;
    _stopLiveRaf();

    // Seek video to lap start: vid_t = sync_offset + lap.elapsed_start
    const vid = _liveVideo();
    if (vid && vid.readyState >= 1 && vid.duration) {
      const lap    = _liveLaps[lapIdx];
      const seekTo = _liveOffset + (lap.elapsed_start || 0);
      console.log('[switchLap]', lapIdx, '| _liveOffset:', _liveOffset, '| elapsed_start:', lap.elapsed_start, '| seekTo:', seekTo, '| duration:', vid.duration);
      vid.currentTime = Math.max(0, Math.min(vid.duration, seekTo));
      const scrub = _container?.querySelector('#live-scrub');
      if (scrub) scrub.value = Math.round(vid.currentTime * 1000);
    }

    // Keep State in sync so Export page sees the right lap
    State.set('previewSession', {
      ...(State.get('previewSession') || {}),
      lap_idx: lapIdx,
    });

    _updateLapSelector();
    await _loadLapData(lapIdx);
    _startLiveRaf();
  }

  // ── Stage current lap for export ───────────────────────────────────────────
  function _stageLapForExport() {
    if (!_liveSession) return;
    const lap   = _liveLaps?.[_selLapIdx];
    const meta  = _liveSessionMeta || {};
    const scope = State.get('exportScope') || 'selected_lap';
    const durStr = lap?.duration != null ? _fmtLapTime(lap.duration) : '?';
    const lapLabel = [
      meta.track || '',
      `Lap ${_selLapIdx + 1}${lap?.is_best ? ' ★' : ''}`,
      durStr,
    ].filter(Boolean).join(' — ');

    const item = {
      csv_path:    _liveSession.csv_path,
      lap_idx:     _selLapIdx,
      video_paths: _liveSession.video_paths || [],
      sync_offset: _liveSession.sync_offset ?? 0,
      source:      _liveSession.source || '',
      duration:    lap?.duration ?? null,
      is_best:     lap?.is_best ?? false,
      track:       meta.track || '',
      csv_start:   _liveSession.csv_start || null,
      lap_label:   lapLabel,
      scope,
    };
    const current = State.get('selectedItems') || [];
    // Avoid duplicates
    const exists = current.some(x => x.csv_path === item.csv_path && x.lap_idx === item.lap_idx);
    if (!exists) State.set('selectedItems', [...current, item]);

    // Brief visual feedback on button
    const btn = _container?.querySelector('#stage-export-btn');
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = '✓ Added';
      btn.disabled = true;
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1500);
    }
  }

  // ── Load full session (metadata + laps + first lap telemetry) ──────────────
  async function loadLiveSession(session) {
    const myGen  = _mountGen;  // bail out if unmounted before we finish
    _liveSession = session;
    _liveOffset  = session.sync_offset ?? 0;

    // Fetch metadata and lap list in parallel
    const [meta, laps] = await Promise.all([
      API.getSessionMeta(session.csv_path).catch(() => ({})),
      API.getLaps(session.csv_path).catch(() => []),
    ]);
    if (_mountGen !== myGen) return;  // navigated away while awaiting

    _liveSessionMeta = meta;
    _liveLaps        = laps;
    console.log('[loadLiveSession] _liveOffset:', _liveOffset, '| laps:', JSON.stringify(laps.map(l => ({idx: l.lap_idx, es: l.elapsed_start, outlap: l.is_outlap, inlap: l.is_inlap, dur: l.duration}))));

    // Default to the lap requested, but skip the outlap — start on the first timed lap.
    let startIdx = session.lap_idx ?? 0;
    if (laps[startIdx]?.is_outlap || laps[startIdx]?.is_inlap) {
      const timedIdx = laps.findIndex(l => !l.is_outlap && !l.is_inlap);
      if (timedIdx >= 0) startIdx = timedIdx;
    }
    _selLapIdx = startIdx;

    // Seek video to the initial lap's start position if video is already loaded.
    // (vid_t = sync_offset + lap.elapsed_start — same formula as switchLap)
    const vid = _liveVideo();
    if (vid && laps?.length && vid.readyState >= 1 && vid.duration) {
      const lap    = laps[_selLapIdx] || laps[0];
      const seekTo = _liveOffset + (lap.elapsed_start || 0);
      vid.currentTime = Math.max(0, Math.min(vid.duration, seekTo));
      const scrub = _container?.querySelector('#live-scrub');
      if (scrub) scrub.value = Math.round(vid.currentTime * 1000);
    }

    _updateLapSelector();
    await _loadLapData(_selLapIdx, myGen);
    if (_mountGen !== myGen) return;
    _startLiveRaf();
  }

  function _fmtVTime(t) {
    const m = Math.floor(t / 60);
    const s = (t % 60).toFixed(3).padStart(6, '0');
    return `${m}:${s}`;
  }

  // ── Preview area helpers ────────────────────────────────────────────────────
  function getPreviewEl() { return _container?.querySelector('#preview-area'); }

  function previewDims() {
    const el = getPreviewEl();
    if (!el) return {w: 1280, h: 720};
    return { w: el.offsetWidth, h: el.offsetHeight };
  }

  // ── Gauge canvas rendering ─────────────────────────────────────────────────
  function renderGaugeEl(gEl, gauge) {
    const {w, h} = previewDims();
    const gw = Math.max(32, Math.round(gauge.w * w));
    const gh = Math.max(24, Math.round(gauge.h * h));

    gEl.width  = gw;
    gEl.height = gh;
    gEl.style.left = `${gauge.x * 100}%`;
    gEl.style.top  = `${gauge.y * 100}%`;
    gEl.style.width  = `${gauge.w * 100}%`;
    gEl.style.height = `${gauge.h * 100}%`;

    const ctx = gEl.getContext('2d');
    ctx.clearRect(0, 0, gw, gh);

    const renderer = GAUGE_RENDERERS[gauge.style];
    if (!renderer) return;

    try {
      const data = (_livePoints && _livePoints.length)
        ? buildLiveData(gauge.channel, gauge.style, _liveFrameIdx, gauge)
        : dummyData(gauge.channel, gauge.style, _layout?.theme || 'Dark', gauge);
      renderer(ctx, data, gw, gh);
    } catch (e) {
      console.error('[renderGaugeEl] channel:', gauge.channel, 'style:', gauge.style, e);
      // Draw error placeholder
      ctx.fillStyle = 'rgba(239,68,68,0.4)';
      ctx.fillRect(0, 0, gw, gh);
      ctx.fillStyle = 'white';
      ctx.font = `${Math.max(8, Math.round(Math.min(gw, gh) * 0.12))}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('Error', gw / 2, gh / 2);
    }
  }

  function rebuildGaugeCanvases() {
    const area = getPreviewEl();
    if (!area || !_layout) return;

    // Remove old gauge canvases and resize handles
    area.querySelectorAll('.gauge-canvas, .resize-handle').forEach(el => el.remove());

    _layout.gauges.forEach((g, idx) => {
      if (!g.visible) return;

      const canvas = document.createElement('canvas');
      canvas.className = 'gauge-canvas';
      canvas.dataset.gaugeIdx = idx;
      canvas.style.cssText = `
        position: absolute;
        cursor: move;
        box-sizing: border-box;
        z-index: 2;
      `;
      canvas.style.outline = (idx === _selected)
        ? '2px solid var(--acc)'
        : '1px solid rgba(255,255,255,0.1)';

      area.appendChild(canvas);
      renderGaugeEl(canvas, g);
    });

    // Draw resize handles as siblings in the area (NOT children of canvas,
    // which can swallow pointer events and clip overflow).
    _layout.gauges.forEach((g, idx) => {
      if (!g.visible || idx !== _selected) return;
      const handle = document.createElement('div');
      handle.className = 'resize-handle';
      handle.dataset.gaugeIdx = idx;
      handle.style.cssText = `
        position: absolute;
        left: ${(g.x + g.w) * 100}%;
        top: ${(g.y + g.h) * 100}%;
        transform: translate(-50%, -50%);
        width: 12px;
        height: 12px;
        background: var(--acc);
        border: 2px solid white;
        border-radius: 2px;
        cursor: se-resize;
        z-index: 10;
        box-sizing: border-box;
      `;
      area.appendChild(handle);
    });
  }

  function rerenderAll() {
    const area = getPreviewEl();
    if (!area || !_layout) return;
    area.querySelectorAll('.gauge-canvas').forEach(el => {
      const idx = parseInt(el.dataset.gaugeIdx);
      if (!isNaN(idx) && _layout.gauges[idx]) {
        renderGaugeEl(el, _layout.gauges[idx]);
      }
    });
  }

  // ── Snap guides ─────────────────────────────────────────────────────────────

  function _clearGuides() {
    const area = getPreviewEl();
    if (!area) return;
    area.querySelectorAll('.snap-guide').forEach(el => el.remove());
  }

  function _showGuide(area, axis, pos) {
    // axis: 'x' = vertical line at normalised x, 'y' = horizontal line at normalised y
    const el = document.createElement('div');
    el.className = 'snap-guide';
    el.style.cssText = `
      position:absolute; pointer-events:none; z-index:20;
      background:transparent;
      border-${axis === 'x' ? 'left' : 'top'}:1px dashed cyan;
      ${axis === 'x'
        ? `left:${pos * 100}%; top:0; width:0; height:100%;`
        : `top:${pos * 100}%; left:0; height:0; width:100%;`}
    `;
    area.appendChild(el);
  }

  // ── Snap logic ───────────────────────────────────────────────────────────────

  /**
   * Snap a value to: canvas edges (0,1), element edges of other gauges,
   * and optionally a size grid. Returns {val, snapped}.
   *
   * edges: array of normalised positions to snap to
   * threshold: snap distance
   * gridStep: if >0, also snap to nearest multiple of gridStep
   */
  function _snapVal(raw, edges, threshold, gridStep = 0) {
    let best = Infinity, snappedTo = null;
    for (const e of edges) {
      const d = Math.abs(raw - e);
      if (d < threshold && d < best) { best = d; snappedTo = e; }
    }
    if (gridStep > 0) {
      const gridSnap = Math.round(raw / gridStep) * gridStep;
      const d = Math.abs(raw - gridSnap);
      if (d < threshold && d < best) { best = d; snappedTo = gridSnap; }
    }
    return snappedTo !== null ? { val: snappedTo, snapped: true } : { val: raw, snapped: false };
  }

  function _applySnap(g, type, draggedIdx) {
    const area   = getPreviewEl();
    const guides = { x: new Set(), y: new Set() };

    // Collect edge snap positions: canvas boundaries
    const xEdges = [0, 1];
    const yEdges = [0, 1];

    // Collect element-edge snap positions from other gauges
    for (let i = 0; i < _layout.gauges.length; i++) {
      if (i === draggedIdx) continue;
      const o = _layout.gauges[i];
      xEdges.push(o.x, o.x + o.w, o.x + o.w / 2);
      yEdges.push(o.y, o.y + o.h, o.y + o.h / 2);
    }
    // Also snap to canvas centre
    xEdges.push(0.5); yEdges.push(0.5);

    if (type === 'move') {
      // Snap left edge, right edge, horizontal centre
      const lSnap = _snapVal(g.x,           xEdges, SNAP_NORM);
      const rSnap = _snapVal(g.x + g.w,     xEdges, SNAP_NORM);
      const cxSnp = _snapVal(g.x + g.w / 2, xEdges, SNAP_NORM);

      if (lSnap.snapped)      { g.x = lSnap.val;           guides.x.add(lSnap.val); }
      else if (rSnap.snapped) { g.x = rSnap.val - g.w;     guides.x.add(rSnap.val); }
      else if (cxSnp.snapped) { g.x = cxSnp.val - g.w / 2; guides.x.add(cxSnp.val); }

      const tSnap = _snapVal(g.y,           yEdges, SNAP_NORM);
      const bSnap = _snapVal(g.y + g.h,     yEdges, SNAP_NORM);
      const cySnp = _snapVal(g.y + g.h / 2, yEdges, SNAP_NORM);

      if (tSnap.snapped)      { g.y = tSnap.val;           guides.y.add(tSnap.val); }
      else if (bSnap.snapped) { g.y = bSnap.val - g.h;     guides.y.add(bSnap.val); }
      else if (cySnp.snapped) { g.y = cySnp.val - g.h / 2; guides.y.add(cySnp.val); }

    } else { // resize
      const wSnap = _snapVal(g.w, [], SNAP_NORM, SNAP_SIZE_STEP);
      const hSnap = _snapVal(g.h, [], SNAP_NORM, SNAP_SIZE_STEP);
      // Also snap right edge to element edges
      const rSnap = _snapVal(g.x + g.w, xEdges, SNAP_ELEM_NORM);
      const bSnap = _snapVal(g.y + g.h, yEdges, SNAP_ELEM_NORM);

      if (rSnap.snapped) { g.w = rSnap.val - g.x; guides.x.add(rSnap.val); }
      else if (wSnap.snapped) { g.w = wSnap.val; }

      if (bSnap.snapped) { g.h = bSnap.val - g.y; guides.y.add(bSnap.val); }
      else if (hSnap.snapped) { g.h = hSnap.val; }
    }

    // Clamp after snap
    g.x = Math.max(0, Math.min(1 - g.w, g.x));
    g.y = Math.max(0, Math.min(1 - g.h, g.y));
    g.w = Math.max(MIN_NORM, Math.min(1 - g.x, g.w));
    g.h = Math.max(MIN_NORM, Math.min(1 - g.y, g.h));

    // Draw guides
    if (area) {
      _clearGuides();
      guides.x.forEach(v => _showGuide(area, 'x', v));
      guides.y.forEach(v => _showGuide(area, 'y', v));
    }
  }

  // ── Mouse events ────────────────────────────────────────────────────────────
  function setupMouseEvents() {
    const area = getPreviewEl();
    if (!area) return;

    area.addEventListener('mousedown', e => {
      const canvas = e.target.closest('.gauge-canvas');
      const handle = e.target.closest('.resize-handle');

      if (handle) {
        const idx = parseInt(handle.dataset.gaugeIdx);
        if (!isNaN(idx)) {
          _drag = {
            type: 'resize',
            gaugeIdx: idx,
            startMx: e.clientX,
            startMy: e.clientY,
            startG: { ...(_layout.gauges[idx]) },
          };
          e.preventDefault();
          e.stopPropagation();
        }
        return;
      }

      if (canvas) {
        const idx = parseInt(canvas.dataset.gaugeIdx);
        if (!isNaN(idx)) {
          selectGauge(idx);
          _drag = {
            type: 'move',
            gaugeIdx: idx,
            startMx: e.clientX,
            startMy: e.clientY,
            startG: { ...(_layout.gauges[idx]) },
          };
          e.preventDefault();
        }
        return;
      }

      // Click on background → deselect
      selectGauge(null);
    });

    document.addEventListener('mousemove', e => {
      if (!_drag) return;
      const {w, h} = previewDims();
      const dx = (e.clientX - _drag.startMx) / w;
      const dy = (e.clientY - _drag.startMy) / h;
      const g  = _layout.gauges[_drag.gaugeIdx];
      const sg = _drag.startG;

      if (_drag.type === 'move') {
        g.x = Math.max(0, Math.min(1 - g.w, sg.x + dx));
        g.y = Math.max(0, Math.min(1 - g.h, sg.y + dy));
      } else {
        g.w = Math.max(MIN_NORM, sg.w + dx);
        g.h = Math.max(MIN_NORM, sg.h + dy);
        g.w = Math.min(g.w, 1 - g.x);
        g.h = Math.min(g.h, 1 - g.y);
      }

      // Apply snapping (modifies g in-place, draws guide lines)
      _applySnap(g, _drag.type, _drag.gaugeIdx);

      // Update canvas position directly (fast path — no full rebuild)
      const canvas = area.querySelector(`.gauge-canvas[data-gauge-idx="${_drag.gaugeIdx}"]`);
      if (canvas) {
        canvas.style.left   = `${g.x * 100}%`;
        canvas.style.top    = `${g.y * 100}%`;
        canvas.style.width  = `${g.w * 100}%`;
        canvas.style.height = `${g.h * 100}%`;
      }
      // Keep the resize handle at the bottom-right corner
      const rHandle = area.querySelector(`.resize-handle[data-gauge-idx="${_drag.gaugeIdx}"]`);
      if (rHandle) {
        rHandle.style.left = `${(g.x + g.w) * 100}%`;
        rHandle.style.top  = `${(g.y + g.h) * 100}%`;
      }
      updatePropPanel();
    });

    document.addEventListener('mouseup', e => {
      if (!_drag) return;
      _drag = null;
      _clearGuides();
      rebuildGaugeCanvases();   // re-render at correct size after resize
      saveLayout();
    });
  }

  // ── Selection ───────────────────────────────────────────────────────────────
  function selectGauge(idx) {
    _selected = idx;
    rebuildGaugeCanvases();
    updatePropPanel();
  }

  // ── Channel-specific property HTML ──────────────────────────────────────────

  const _INFO_FIELDS_ALL = [
    { key: 'track',    label: 'Track' },
    { key: 'datetime', label: 'Date / Time' },
    { key: 'vehicle',  label: 'Vehicle' },
    { key: 'session',  label: 'Session' },
    { key: 'weather',  label: 'Weather' },
    { key: 'wind',     label: 'Wind' },
  ];

  function _buildChannelProps(g) {
    if (g.channel === 'info') {
      const sel = g.selected_fields || ['track','datetime','vehicle','weather','wind'];
      const ov  = g.info_overrides  || {};
      return `
        <div style="border-top:1px solid var(--border);padding-top:8px;margin-top:4px;">
          <div style="font-size:9px;color:var(--text3);margin-bottom:6px;
                      text-transform:uppercase;letter-spacing:0.04em;">Fields &amp; Overrides</div>
          ${_INFO_FIELDS_ALL.map(f => `
            <div style="display:flex;align-items:center;gap:5px;margin-bottom:5px;">
              <input type="checkbox" class="info-field-chk" data-field="${f.key}"
                     ${sel.includes(f.key) ? 'checked' : ''}
                     style="flex-shrink:0;margin:0;">
              <span style="font-size:10px;color:var(--text2);width:65px;flex-shrink:0">${f.label}</span>
              <input type="text" class="info-ov-input" data-field="${f.key}"
                     value="${_esc(ov[f.key] || '')}" placeholder="from session"
                     style="flex:1;font-size:9px;font-family:var(--mono);min-width:0;">
            </div>
          `).join('')}
        </div>`;
    }

    if (g.channel === 'lap_info') {
      const LAP_INFO_ROWS = [
        { key: 'lap',     label: 'Lap #' },
        { key: 'best',    label: 'Best' },
        { key: 'current', label: 'Current' },
        { key: 'delta',   label: 'Delta' },
      ];
      const sel = g.selected_fields || ['lap','best','current','delta'];
      return `
        <div style="border-top:1px solid var(--border);padding-top:8px;margin-top:4px;">
          <div style="font-size:9px;color:var(--text3);margin-bottom:6px;
                      text-transform:uppercase;letter-spacing:0.04em;">Rows to show</div>
          ${LAP_INFO_ROWS.map(f => `
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">
              <input type="checkbox" class="lapinfo-field-chk" data-field="${f.key}"
                     ${sel.includes(f.key) ? 'checked' : ''}
                     style="flex-shrink:0;margin:0;">
              <span style="font-size:10px;color:var(--text2);">${f.label}</span>
            </div>
          `).join('')}
        </div>`;
    }

    if (g.channel === 'image') {
      const path    = g.image_path || '';
      const opacity = Math.round((g.opacity ?? 1.0) * 100);
      const fit     = g.fit || 'contain';
      return `
        <div style="border-top:1px solid var(--border);padding-top:8px;margin-top:4px;">
          <div style="font-size:9px;color:var(--text3);margin-bottom:6px;
                      text-transform:uppercase;letter-spacing:0.04em;">Image File</div>
          <div style="display:flex;gap:4px;align-items:center;">
            <input type="text" id="img-path-inp" class="input-field"
                   value="${_esc(path)}" placeholder="C:\\path\\to\\logo.png"
                   style="flex:1;font-size:9px;font-family:var(--mono);min-width:0;">
            <button class="btn btn-sm" id="img-browse-btn" style="flex-shrink:0;">Browse</button>
          </div>
          <div style="display:flex;gap:8px;align-items:center;margin-top:8px;">
            <label style="font-size:9px;color:var(--text2);white-space:nowrap;">Opacity</label>
            <input type="range" id="img-opacity" min="0" max="100" value="${opacity}"
                   style="flex:1;">
            <span id="img-opacity-val" style="font-size:9px;color:var(--text);width:28px;text-align:right;">${opacity}%</span>
          </div>
          <div style="display:flex;gap:8px;align-items:center;margin-top:6px;">
            <label style="font-size:9px;color:var(--text2);white-space:nowrap;">Fit</label>
            <select id="img-fit" style="flex:1;font-size:10px;">
              <option value="contain" ${fit==='contain'?'selected':''}>Contain (letterbox)</option>
              <option value="cover"   ${fit==='cover'  ?'selected':''}>Cover (crop)</option>
              <option value="stretch" ${fit==='stretch'?'selected':''}>Stretch</option>
            </select>
          </div>
          <div style="font-size:9px;color:var(--text3);margin-top:5px;">PNG with alpha recommended.</div>
        </div>`;
    }

    if (g.channel === 'map' && g.style === 'Zoomed') {
      const radius  = g.zoom_radius_m ?? 150;
      const showRef = g.show_ref !== false;
      return `
        <div style="border-top:1px solid var(--border);padding-top:8px;margin-top:4px;">
          <div style="font-size:9px;color:var(--text3);margin-bottom:6px;
                      text-transform:uppercase;letter-spacing:0.04em;">Zoom Settings</div>
          <div class="form-row">
            <span class="form-label">Radius&nbsp;(m)</span>
            <input type="number" id="map-radius" value="${radius}"
                   min="10" max="5000" step="10"
                   style="width:70px;font-variant-numeric:tabular-nums;">
          </div>
          <div class="form-row" style="margin-top:6px;">
            <span class="form-label">Show ref lap</span>
            <input type="checkbox" id="map-show-ref" ${showRef ? 'checked' : ''}>
          </div>
          <div style="font-size:9px;color:var(--text3);margin-top:5px;">
            Reference lap trace shown in purple.<br>
            Ref GPS loaded when a ref lap is set.
          </div>
        </div>`;
    }

    if (g.channel === 'multi') {
      const keys = g.multi_channels || ['speed', 'gforce_lat'];
      const opts = MULTI_CHANNEL_OPTS.map(o =>
        `<option value="${o.value}">${o.label}</option>`).join('');
      return `
        <div style="border-top:1px solid var(--border);padding-top:8px;margin-top:4px;">
          <div style="font-size:9px;color:var(--text3);margin-bottom:6px;
                      text-transform:uppercase;letter-spacing:0.04em;">Channels</div>
          <div id="multi-ch-list">
            ${keys.map((ch, i) => {
              const lbl = MULTI_CHANNEL_OPTS.find(o => o.value === ch)?.label || ch;
              return `<div style="display:flex;align-items:center;gap:4px;margin-bottom:4px;">
                <div style="width:10px;height:10px;border-radius:2px;flex-shrink:0;
                            background:${GAUGE_COLOURS_LIST[i % GAUGE_COLOURS_LIST.length]}"></div>
                <span style="flex:1;font-size:10px;color:var(--text)">${lbl}</span>
                <button class="btn btn-sm multi-rm-btn" data-mch="${ch}"
                        style="padding:1px 6px;color:var(--err);border-color:var(--err);">✕</button>
              </div>`;
            }).join('')}
          </div>
          <div style="display:flex;gap:4px;margin-top:6px;">
            <select id="multi-add-sel" style="flex:1;font-size:10px;">${opts}</select>
            <button class="btn btn-sm" id="multi-add-btn" style="flex-shrink:0;">+ Add</button>
          </div>
        </div>`;
    }

    return '';
  }

  function _bindChannelPropEvents(panel, g) {
    if (g.channel === 'image') {
      const inp       = panel.querySelector('#img-path-inp');
      const browseBtn = panel.querySelector('#img-browse-btn');
      const opSlider  = panel.querySelector('#img-opacity');
      const opVal     = panel.querySelector('#img-opacity-val');
      const fitSel    = panel.querySelector('#img-fit');

      const _applyPath = (path) => {
        // Clear cached image so the new path loads fresh
        if (_livePort && g.image_path) {
          const oldUrl = `http://127.0.0.1:${_livePort}/?f=${encodeURIComponent(g.image_path)}`;
          GaugeImage.clearCache(oldUrl);
        }
        g.image_path = path;
        rebuildGaugeCanvases();
        saveLayout();
      };

      inp?.addEventListener('change', () => _applyPath(inp.value.trim()));

      browseBtn?.addEventListener('click', async () => {
        const path = await API.openFileDialog(
          ['Image Files (*.png *.jpg *.jpeg *.webp *.bmp)']
        );
        if (path) { inp.value = path; _applyPath(path); }
      });

      opSlider?.addEventListener('input', () => {
        const pct = parseInt(opSlider.value);
        if (opVal) opVal.textContent = pct + '%';
        g.opacity = pct / 100;
        rebuildGaugeCanvases();
        saveLayout();
      });

      fitSel?.addEventListener('change', () => {
        g.fit = fitSel.value;
        rebuildGaugeCanvases();
        saveLayout();
      });
    }

    if (g.channel === 'map' && g.style === 'Zoomed') {
      panel.querySelector('#map-radius')?.addEventListener('change', e => {
        g.zoom_radius_m = Math.max(10, Math.min(5000, parseInt(e.target.value) || 150));
        rebuildGaugeCanvases();
        saveLayout();
      });
      panel.querySelector('#map-show-ref')?.addEventListener('change', e => {
        g.show_ref = e.target.checked;
        rebuildGaugeCanvases();
        saveLayout();
      });
    }

    if (g.channel === 'lap_info') {
      panel.querySelectorAll('.lapinfo-field-chk').forEach(chk => {
        chk.addEventListener('change', () => {
          const checked = [...panel.querySelectorAll('.lapinfo-field-chk')]
            .filter(c => c.checked).map(c => c.dataset.field);
          g.selected_fields = checked;
          rebuildGaugeCanvases();
          saveLayout();
        });
      });
    }

    if (g.channel === 'info') {
      panel.querySelectorAll('.info-field-chk').forEach(chk => {
        chk.addEventListener('change', () => {
          const checked = [...panel.querySelectorAll('.info-field-chk')]
            .filter(c => c.checked).map(c => c.dataset.field);
          g.selected_fields = checked;
          rebuildGaugeCanvases();
          saveLayout();
        });
      });

      panel.querySelectorAll('.info-ov-input').forEach(inp => {
        inp.addEventListener('change', () => {
          if (!g.info_overrides) g.info_overrides = {};
          const val = inp.value.trim();
          if (val) g.info_overrides[inp.dataset.field] = val;
          else     delete g.info_overrides[inp.dataset.field];
          rebuildGaugeCanvases();
          saveLayout();
        });
      });
    }

    if (g.channel === 'multi') {
      const _rebuildMulti = () => {
        // Rebuild just the channel list DOM (no full prop panel refresh)
        const listEl = panel.querySelector('#multi-ch-list');
        if (!listEl) return;
        const keys = g.multi_channels || [];
        listEl.innerHTML = keys.map((ch, i) => {
          const lbl = MULTI_CHANNEL_OPTS.find(o => o.value === ch)?.label || ch;
          return `<div style="display:flex;align-items:center;gap:4px;margin-bottom:4px;">
            <div style="width:10px;height:10px;border-radius:2px;flex-shrink:0;
                        background:${GAUGE_COLOURS_LIST[i % GAUGE_COLOURS_LIST.length]}"></div>
            <span style="flex:1;font-size:10px;color:var(--text)">${lbl}</span>
            <button class="btn btn-sm multi-rm-btn" data-mch="${ch}"
                    style="padding:1px 6px;color:var(--err);border-color:var(--err);">✕</button>
          </div>`;
        }).join('');
        listEl.querySelectorAll('.multi-rm-btn').forEach(btn => {
          btn.addEventListener('click', () => {
            const ch = btn.dataset.mch;
            g.multi_channels = (g.multi_channels || []).filter(c => c !== ch);
            _rebuildMulti();
            rebuildGaugeCanvases();
            saveLayout();
          });
        });
      };

      // Wire existing remove buttons
      panel.querySelectorAll('.multi-rm-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const ch = btn.dataset.mch;
          g.multi_channels = (g.multi_channels || []).filter(c => c !== ch);
          _rebuildMulti();
          rebuildGaugeCanvases();
          saveLayout();
        });
      });

      panel.querySelector('#multi-add-btn')?.addEventListener('click', () => {
        const sel = panel.querySelector('#multi-add-sel');
        const ch  = sel?.value;
        if (!ch) return;
        if (!g.multi_channels) g.multi_channels = [];
        if (!g.multi_channels.includes(ch)) {
          g.multi_channels.push(ch);
          _rebuildMulti();
          rebuildGaugeCanvases();
          saveLayout();
        }
      });
    }
  }

  // ── Properties panel ────────────────────────────────────────────────────────
  function updatePropPanel() {
    const panel = _container?.querySelector('#prop-panel');
    if (!panel) return;

    if (_selected === null || !_layout?.gauges[_selected]) {
      panel.innerHTML = `<div style="color:var(--text3); font-size:11px; padding:12px">
        Select a gauge to edit its properties.</div>`;
      return;
    }

    const g = _layout.gauges[_selected];
    const styles = CHANNEL_STYLES[g.channel] || ['Numeric'];
    const styleOptions = styles.map(s =>
      `<option value="${s}" ${s === g.style ? 'selected' : ''}>${s}</option>`
    ).join('');

    panel.innerHTML = `
      <div style="padding:12px; display:flex; flex-direction:column; gap:8px;">
        <div style="font-size:10px; font-weight:700; color:var(--text2);
                    text-transform:uppercase; letter-spacing:0.04em; margin-bottom:4px">
          Gauge Properties
        </div>

        <div class="form-row">
          <span class="form-label">Channel</span>
          <select id="prop-channel" style="flex:1">
            ${ALL_CHANNELS.map(c => `<option value="${c.value}" ${c.value===g.channel?'selected':''}>${c.label}</option>`).join('')}
          </select>
        </div>

        <div class="form-row">
          <span class="form-label">Style</span>
          <select id="prop-style" style="flex:1">${styleOptions}</select>
        </div>

        <div class="form-row">
          <span class="form-label">Visible</span>
          <input type="checkbox" id="prop-visible" ${g.visible !== false ? 'checked' : ''}>
        </div>

        <div style="border-top:1px solid var(--border); padding-top:8px; margin-top:4px;">
          <div style="font-size:9px; color:var(--text3); margin-bottom:6px;">Position (normalised 0–1)</div>
          <div class="form-row">
            <span class="form-label" style="min-width:20px">X</span>
            <input type="number" id="prop-x" value="${g.x.toFixed(3)}" step="0.01" style="width:70px">
            <span class="form-label" style="min-width:20px">Y</span>
            <input type="number" id="prop-y" value="${g.y.toFixed(3)}" step="0.01" style="width:70px">
          </div>
          <div class="form-row">
            <span class="form-label" style="min-width:20px">W</span>
            <input type="number" id="prop-w" value="${g.w.toFixed(3)}" step="0.01" style="width:70px">
            <span class="form-label" style="min-width:20px">H</span>
            <input type="number" id="prop-h" value="${g.h.toFixed(3)}" step="0.01" style="width:70px">
          </div>
        </div>

        ${_buildChannelProps(g)}

        <button class="btn btn-sm" id="prop-delete"
                style="margin-top:8px; border-color:var(--err); color:var(--err);">
          Remove Gauge
        </button>
      </div>`;

    // Wire up change handlers
    panel.querySelector('#prop-channel').addEventListener('change', e => {
      g.channel = e.target.value;
      const newStyles = CHANNEL_STYLES[g.channel] || ['Numeric'];
      g.style = newStyles[0];
      // Apply per-channel defaults if not already set
      const defs = _channelDefaults(g.channel);
      for (const [k, v] of Object.entries(defs)) {
        if (g[k] === undefined) g[k] = v;
      }
      selectGauge(_selected);  // refresh (updates style dropdown too)
      saveLayout();
    });

    panel.querySelector('#prop-style').addEventListener('change', e => {
      g.style = e.target.value;
      updatePropPanel();   // refresh channel-specific props (e.g. Zoomed map settings)
      rebuildGaugeCanvases();
      saveLayout();
    });

    panel.querySelector('#prop-visible').addEventListener('change', e => {
      g.visible = e.target.checked;
      rebuildGaugeCanvases();
      saveLayout();
    });

    for (const key of ['x', 'y', 'w', 'h']) {
      panel.querySelector(`#prop-${key}`).addEventListener('change', e => {
        g[key] = Math.max(0, Math.min(1, parseFloat(e.target.value) || 0));
        rebuildGaugeCanvases();
        saveLayout();
      });
    }

    panel.querySelector('#prop-delete').addEventListener('click', () => {
      _layout.gauges.splice(_selected, 1);
      _selected = null;
      rebuildGaugeCanvases();
      rebuildGaugeList();
      updatePropPanel();
      saveLayout();
    });

    _bindChannelPropEvents(panel, g);
  }

  // ── Gauge list (left sidebar) ───────────────────────────────────────────────
  function rebuildGaugeList() {
    const list = _container?.querySelector('#gauge-list');
    if (!list || !_layout) return;

    list.innerHTML = _layout.gauges.map((g, idx) => {
      const ch      = ALL_CHANNELS.find(c => c.value === g.channel)?.label || g.channel;
      const col     = GAUGE_COLOURS_LIST[idx % GAUGE_COLOURS_LIST.length];
      const visible = g.visible !== false;
      return `
        <div class="gauge-list-item ${idx === _selected ? 'selected' : ''}"
             data-idx="${idx}"
             style="display:flex; align-items:center; gap:8px; padding:7px 12px;
                    cursor:pointer; border-bottom:1px solid var(--border);
                    ${idx === _selected ? 'background:rgba(79,142,247,0.12)' : ''}">
          <div style="width:8px; height:8px; border-radius:50%;
                      background:${col}; flex-shrink:0;"></div>
          <div style="flex:1; min-width:0;">
            <div style="font-size:11px; color:var(--text); white-space:nowrap;
                        overflow:hidden; text-overflow:ellipsis;
                        ${visible ? '' : 'opacity:0.4;'}">${ch}</div>
            <div style="font-size:9px; color:var(--text3)">${g.style}</div>
          </div>
          <button class="vis-toggle" data-vis-idx="${idx}"
                  title="${visible ? 'Hide gauge' : 'Show gauge'}"
                  style="background:none;border:none;cursor:pointer;
                         font-size:13px;padding:2px 4px;color:var(--text2);
                         flex-shrink:0;line-height:1;">${visible ? '●' : '○'}</button>
        </div>`;
    }).join('');

    list.querySelectorAll('.gauge-list-item').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.closest('.vis-toggle')) return; // handled separately
        selectGauge(parseInt(el.dataset.idx));
      });
    });

    list.querySelectorAll('.vis-toggle').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.visIdx);
        if (!isNaN(idx) && _layout.gauges[idx]) {
          _layout.gauges[idx].visible = !(_layout.gauges[idx].visible !== false);
          rebuildGaugeList();
          rebuildGaugeCanvases();
          saveLayout();
        }
      });
    });
  }

  // ── Add gauge ───────────────────────────────────────────────────────────────
  function _channelDefaults(channel) {
    if (channel === 'info')  return { selected_fields: ['track','datetime','vehicle','weather','wind'], info_overrides: {} };
    if (channel === 'multi') return { multi_channels: ['speed', 'gforce_lat'] };
    if (channel === 'image') return { image_path: '', opacity: 1.0, fit: 'contain' };
    if (channel === 'map')   return { zoom_radius_m: 150, show_ref: true };
    return {};
  }

  function addGauge() {
    const newG = {
      channel: 'speed',
      style:   'Dial',
      visible: true,
      x: 0.01,
      y: 0.74,
      w: 0.13,
      h: 0.23,
    };
    _layout.gauges.push(newG);
    selectGauge(_layout.gauges.length - 1);
    rebuildGaugeList();
    saveLayout();
  }

  // ── Preset management ────────────────────────────────────────────────────────
  async function loadPresetList() {
    try {
      _presets = await API.listPresets();
      rebuildPresetSelector();
    } catch (err) {
      console.warn('Failed to load preset list:', err);
    }
  }

  function rebuildPresetSelector() {
    const sel = _container?.querySelector('#preset-select');
    if (!sel) return;
    const cur = _layout?.active_preset || '';
    sel.innerHTML = `<option value="">— No Preset —</option>` +
      _presets.map(p => `<option value="${p}" ${p===cur?'selected':''}>${p}</option>`).join('');
  }

  async function saveAsPreset() {
    const name = prompt('Preset name:');
    if (!name) return;
    await API.saveOverlayAs(name, _layout);
    _layout.active_preset = name;
    await loadPresetList();
  }

  // ── Theme selector ───────────────────────────────────────────────────────────
  function rebuildThemeSelector() {
    const sel = _container?.querySelector('#theme-select');
    if (!sel || !_layout) return;
    ['Dark', 'Light', 'Colorful', 'Monochrome', 'Minimal'].forEach(t => {
      sel.querySelector(`option[value="${t}"]`)?.setAttribute(
        'selected', t === _layout.theme ? '' : null);
    });
    sel.value = _layout.theme;
  }

  // ── Save layout ──────────────────────────────────────────────────────────────
  async function saveLayout() {
    try {
      await API.saveOverlay(_layout);
    } catch (err) {
      console.error('saveLayout failed:', err);
    }
  }

  // ── Mount ────────────────────────────────────────────────────────────────────
  async function mount(container) {
    const myGen = _mountGen;  // capture generation — if unmount runs before we finish, myGen !== _mountGen
    _container = container;
    _selected  = null;

    // ── Resolve video src BEFORE rendering HTML (mirrors data page approach) ──
    const prevSession = State.get('previewSession');
    // Only fetch port once — the server never changes address, and re-calling on fast
    // re-navigation can fail/reject and zero out _livePort, hiding the video element.
    if (!_livePort) _livePort = await API.getVideoServerPort().catch(() => 0);
    if (_mountGen !== myGen) return;  // navigated away while awaiting port
    _liveOffset = prevSession?.sync_offset ?? 0;

    const vp = prevSession?.video_paths?.[0] ?? null;
    const videoSrc = (vp && _livePort)
      ? `http://127.0.0.1:${_livePort}/?f=${encodeURIComponent(vp)}`
      : '';

    const hasVideo   = !!videoSrc;
    const hintText   = hasVideo ? '' : 'Select a session on the Data page, then click Open in Overlay →';
    const videoStyle = `position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:0;opacity:${hasVideo ? '0.9' : '0'}`;

    // Use persisted video dimensions for the initial aspect-ratio so there is no
    // 16/9 flash on re-mount even when the browser returns metadata from cache.
    const initAspect = (prevSession?.video_w && prevSession?.video_h)
      ? `${prevSession.video_w} / ${prevSession.video_h}`
      : '16 / 9';

    container.innerHTML = `
      <div style="display:flex; flex-direction:column; height:100vh; overflow:hidden;">

        <!-- Toolbar -->
        <div style="padding:8px 16px; border-bottom:1px solid var(--border);
                    display:flex; align-items:center; gap:8px; flex-shrink:0;
                    background:var(--sidebar);">
          <span style="font-size:12px; font-weight:700; color:var(--text)">Overlay</span>

          <!-- Lap selector -->
          <div style="display:flex;align-items:center;gap:3px;margin-left:8px;flex-shrink:0;">
            <button class="btn btn-sm" id="lap-prev" title="Previous lap"
                    style="padding:2px 8px;" disabled>◀</button>
            <select id="lap-sel" style="font-size:10px;min-width:120px;">
              <option value="">— no session —</option>
            </select>
            <button class="btn btn-sm" id="lap-next" title="Next lap"
                    style="padding:2px 8px;" disabled>▶</button>
          </div>

          <select id="overlay-scope" title="Export scope"
                  style="font-size:10px;flex-shrink:0;min-width:90px;">
            <option value="selected_lap">This lap</option>
            <option value="fastest">Fastest</option>
            <option value="all_laps">All laps</option>
            <option value="full">Full session</option>
          </select>
          <button class="btn btn-sm" id="stage-export-btn"
                  title="Add this lap to the export queue"
                  style="flex-shrink:0;border-color:var(--ok);color:var(--ok);">+ Export</button>

          <div style="flex:1"></div>
          <label style="font-size:10px; color:var(--text2)">Theme</label>
          <select id="theme-select" style="font-size:10px;">
            <option value="Dark">Dark</option>
            <option value="Light">Light</option>
            <option value="Colorful">Colorful</option>
            <option value="Monochrome">Monochrome</option>
            <option value="Minimal">Minimal</option>
          </select>
          <select id="preset-select" style="font-size:10px; max-width:120px">
            <option value="">— No Preset —</option>
          </select>
          <button class="btn btn-sm" id="save-preset-btn">Save As…</button>
          <button class="btn btn-sm btn-accent" id="save-layout-btn">Save</button>
        </div>

        <!-- Main content row -->
        <div style="flex:1; display:flex; overflow:hidden;">

          <!-- Left: video preview + scrub strip -->
          <div style="flex:1; display:flex; flex-direction:column; overflow:hidden; background:var(--bg);">

            <div style="flex:1; display:flex; align-items:center; justify-content:center;
                        padding:16px; overflow:hidden;">
              <div id="preview-area"
                   style="position:relative; aspect-ratio:${initAspect}; width:100%; max-width:100%; max-height:100%;
                          background:#111827; border:1px solid var(--border);
                          border-radius:4px; overflow:hidden;">
                <video id="preview-video" preload="auto"
                       ${videoSrc ? `src="${videoSrc}"` : ''}
                       style="${videoStyle}"></video>
                <div id="preview-hint" style="position:absolute;inset:0;display:flex;
                     align-items:center;justify-content:center;pointer-events:none;z-index:1;
                     ${hasVideo ? 'display:none' : ''}">
                  <span style="font-size:12px;color:rgba(255,255,255,0.2);user-select:none;text-align:center;padding:16px">
                    ${hintText}
                  </span>
                </div>
              </div>
            </div>

            <!-- Scrub strip -->
            <div id="live-strip" style="padding:6px 12px; flex-shrink:0;
                  border-top:1px solid var(--border); background:var(--sidebar);
                  display:flex; align-items:center; gap:8px;">
              <button id="live-play" class="btn btn-sm" style="width:32px;padding:0;flex-shrink:0">▶</button>
              <span id="live-time" style="font-size:10px;color:var(--acc2);
                    font-variant-numeric:tabular-nums;width:64px;flex-shrink:0">0:00.000</span>
              <input type="range" id="live-scrub" min="0" max="1000" step="1" value="0"
                     style="flex:1;accent-color:var(--acc);cursor:pointer;">
              <span id="live-label" style="font-size:9px;color:var(--text3);flex-shrink:0;
                    max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                ${hasVideo ? 'Loading telemetry…' : 'No session loaded'}
              </span>
            </div>
          </div>

          <!-- Right: gauge list + properties -->
          <div style="width:280px;min-width:240px;display:flex;flex-direction:column;
                      border-left:1px solid var(--border);background:var(--sidebar);overflow:hidden;">
            <div style="padding:10px 12px;border-bottom:1px solid var(--border);flex-shrink:0;">
              <button class="btn btn-sm btn-accent" id="add-gauge-btn" style="width:100%">+ Add Gauge</button>
            </div>
            <div id="gauge-list" style="flex:1;overflow-y:auto;min-height:80px;"></div>
            <div id="prop-panel" style="border-top:1px solid var(--border);overflow-y:auto;flex-shrink:0;max-height:55%;">
              <div style="color:var(--text3);font-size:11px;padding:12px">Select a gauge to edit its properties.</div>
            </div>
            <!-- Reference lap -->
            <div style="border-top:1px solid var(--border);padding:10px 12px;flex-shrink:0;">
              <div style="font-size:9px;font-weight:700;color:var(--text2);text-transform:uppercase;
                          letter-spacing:0.05em;margin-bottom:6px;">Reference Lap</div>
              <select id="ref-mode-sel" style="width:100%;font-size:11px;">
                <option value="none">None</option>
                <option value="session_best">Best in session</option>
              </select>
            </div>
          </div>

        </div>
      </div>`;

    // Load overlay layout
    try {
      _layout = await API.getOverlay();
      _layout.gauges = _layout.gauges || [];
    } catch (_) {
      _layout = { is_bike: false, theme: 'Dark', gauges: [] };
    }
    if (_mountGen !== myGen) return;

    await loadPresetList();
    if (_mountGen !== myGen) return;
    rebuildThemeSelector();
    rebuildGaugeList();
    rebuildGaugeCanvases();
    setupMouseEvents();

    // Restore ref_mode from layout
    const refSel = container.querySelector('#ref-mode-sel');
    if (refSel) {
      refSel.value = _layout.ref_mode || 'none';
      refSel.addEventListener('change', e => {
        _layout.ref_mode = e.target.value;
        saveLayout();
      });
    }

    container.querySelector('#add-gauge-btn').addEventListener('click', addGauge);

    container.querySelector('#save-layout-btn').addEventListener('click', async () => {
      await saveLayout();
      const btn = container.querySelector('#save-layout-btn');
      const orig = btn.textContent;
      btn.textContent = 'Saved ✓';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    });

    container.querySelector('#save-preset-btn').addEventListener('click', saveAsPreset);

    container.querySelector('#theme-select').addEventListener('change', e => {
      _layout.theme = e.target.value;
      rebuildGaugeCanvases();
      saveLayout();
    });

    container.querySelector('#preset-select').addEventListener('change', async e => {
      const name = e.target.value;
      if (!name) return;
      const presets = await API.getConfig().then(c => c.presets || {});
      if (presets[name]) {
        _layout = { ...presets[name], active_preset: name };
        _selected = null;
        rebuildThemeSelector();
        rebuildGaugeList();
        rebuildGaugeCanvases();
        updatePropPanel();
        const rs = container.querySelector('#ref-mode-sel');
        if (rs) rs.value = _layout.ref_mode || 'none';
      }
    });

    // Lap selector
    container.querySelector('#lap-sel')?.addEventListener('change', e => {
      const idx = parseInt(e.target.value);
      if (!isNaN(idx)) switchLap(idx);
    });
    container.querySelector('#lap-prev')?.addEventListener('click', () => {
      switchLap(_selLapIdx - 1);
    });
    container.querySelector('#lap-next')?.addEventListener('click', () => {
      switchLap(_selLapIdx + 1);
    });

    // Scope selector → persist in State so Export tab reads it
    const scopeSel = container.querySelector('#overlay-scope');
    if (scopeSel) {
      scopeSel.value = State.get('exportScope') || 'selected_lap';
      scopeSel.addEventListener('change', e => State.set('exportScope', e.target.value));
    }

    // Stage for export
    container.querySelector('#stage-export-btn')?.addEventListener('click', _stageLapForExport);

    _resizeObserver = new ResizeObserver(() => rebuildGaugeCanvases());
    const area = container.querySelector('#preview-area');
    if (area) _resizeObserver.observe(area);

    // Wire video/scrub controls once, then load session data
    _wireVideoControls();
    if (prevSession) loadLiveSession(prevSession);
  }

  function unmount() {
    _mountGen++;   // invalidate all in-flight async ops from the current mount
    if (_animFrame) { cancelAnimationFrame(_animFrame); _animFrame = null; }
    _stopLiveRaf();
    if (_resizeObserver) { _resizeObserver.disconnect(); _resizeObserver = null; }
    _livePoints      = null;
    _liveLats        = null;
    _liveLons        = null;
    _liveSession     = null;
    _liveSessionMeta = null;
    _liveLaps        = null;
    _container       = null;
  }

  Router.register('editor', { mount, unmount });
})();
