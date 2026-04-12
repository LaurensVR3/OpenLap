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
    map:         ['Circuit'],
    info:        ['Info'],
    lap_info:    ['Scoreboard'],
    multi:       ['Multi-Line'],
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
  ];

  const GAUGE_COLOURS_LIST = [
    '#00d4ff','#ff6b35','#a8ff3e','#ff3ea8',
    '#ffd700','#3ea8ff','#ff3e3e','#3effd7',
    '#c084fc','#fb923c',
  ];

  // ── State ──────────────────────────────────────────────────────────────────
  let _layout    = null;   // {is_bike, theme, gauges:[...]}
  let _presets   = [];
  let _container = null;
  let _selected  = null;   // index of selected gauge
  let _drag      = null;   // {type:'move'|'resize', gaugeIdx, startMx, startMy, startG}
  let _lapHistory = null;  // live telemetry data (optional)
  let _animFrame  = null;

  // Constants (normalised)
  const MIN_NORM      = 0.04;
  const SNAP_NORM     = 0.02;
  const HANDLE_NORM   = 0.012;   // resize handle size as fraction of preview width

  // ── Dummy data ─────────────────────────────────────────────────────────────
  function dummyData(channel, style, theme) {
    const base = { theme };
    switch (channel) {
      case 'info': return {
        ...base,
        info_track: 'Spa-Francorchamps',
        info_date: '2024-06-15', info_time: '14:32',
        info_vehicle: 'Porsche 992 GT3 R',
        info_session: 'Practice',
        info_weather: '22°C  Partly cloudy',
        info_wind: 'NW  8 km/h',
        selected_fields: ['track','datetime','vehicle','weather','wind'],
      };
      case 'lap_info': return {
        ...base, lap_num: 3, total_laps: 8,
        lap_elapsed: 45.234, best_so_far: 83.456,
      };
      case 'map': return {
        ...base,
        lats: [], lons: [], cur_idx: 0,
      };
      case 'multi': {
        const t = 0;
        const mh = (amp, off) => Array.from({length:40}, (_,i)=>amp*Math.sin(i*0.25+off)+off);
        return {
          ...base,
          multi_channels: [
            { channel:'speed', label:'Speed', unit:'km/h', values:mh(80,100), value:140, min_val:0, max_val:250, symmetric:false, color_idx:0 },
            { channel:'gforce_lat', label:'Lat G', unit:'G', values:mh(1.5,0), value:0.8, min_val:-3, max_val:3, symmetric:true, color_idx:1 },
          ]
        };
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
      const data = dummyData(gauge.channel, gauge.style, _layout?.theme || 'Dark');
      renderer(ctx, data, gw, gh);
    } catch (e) {
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

    // Remove old gauge canvases
    area.querySelectorAll('.gauge-canvas').forEach(el => el.remove());

    _layout.gauges.forEach((g, idx) => {
      if (!g.visible) return;

      const canvas = document.createElement('canvas');
      canvas.className = 'gauge-canvas';
      canvas.dataset.gaugeIdx = idx;
      canvas.style.cssText = `
        position: absolute;
        cursor: move;
        box-sizing: border-box;
      `;
      canvas.style.outline = (idx === _selected)
        ? '2px solid var(--acc)'
        : '1px solid rgba(255,255,255,0.1)';

      area.appendChild(canvas);
      renderGaugeEl(canvas, g);

      // Draw resize handle (bottom-right corner)
      if (idx === _selected) {
        const handle = document.createElement('div');
        handle.className = 'resize-handle';
        handle.dataset.gaugeIdx = idx;
        handle.style.cssText = `
          position: absolute;
          right: -5px;
          bottom: -5px;
          width: 10px;
          height: 10px;
          background: var(--acc);
          border: 1px solid white;
          cursor: se-resize;
          z-index: 10;
          box-sizing: border-box;
        `;
        canvas.appendChild(handle);
      }
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

      // Update canvas position directly (fast)
      const canvas = area.querySelector(`.gauge-canvas[data-gauge-idx="${_drag.gaugeIdx}"]`);
      if (canvas) {
        canvas.style.left   = `${g.x * 100}%`;
        canvas.style.top    = `${g.y * 100}%`;
        canvas.style.width  = `${g.w * 100}%`;
        canvas.style.height = `${g.h * 100}%`;
      }
      updatePropPanel();
    });

    document.addEventListener('mouseup', e => {
      if (!_drag) return;
      _drag = null;
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
      selectGauge(_selected);  // refresh (updates style dropdown too)
      saveLayout();
    });

    panel.querySelector('#prop-style').addEventListener('change', e => {
      g.style = e.target.value;
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
  }

  // ── Gauge list (left sidebar) ───────────────────────────────────────────────
  function rebuildGaugeList() {
    const list = _container?.querySelector('#gauge-list');
    if (!list || !_layout) return;

    list.innerHTML = _layout.gauges.map((g, idx) => {
      const ch = ALL_CHANNELS.find(c => c.value === g.channel)?.label || g.channel;
      const col = GAUGE_COLOURS_LIST[idx % GAUGE_COLOURS_LIST.length];
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
                        overflow:hidden; text-overflow:ellipsis;">${ch}</div>
            <div style="font-size:9px; color:var(--text3)">${g.style}</div>
          </div>
          <div style="font-size:9px; color:var(--text3)">
            ${g.visible !== false ? '' : '<span style="color:var(--text3)">hidden</span>'}
          </div>
        </div>`;
    }).join('');

    list.querySelectorAll('.gauge-list-item').forEach(el => {
      el.addEventListener('click', () => selectGauge(parseInt(el.dataset.idx)));
    });
  }

  // ── Add gauge ───────────────────────────────────────────────────────────────
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
    } catch (_) {}
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
    ['Dark', 'Light', 'Colorful', 'Monochrome'].forEach(t => {
      sel.querySelector(`option[value="${t}"]`)?.setAttribute(
        'selected', t === _layout.theme ? '' : null);
    });
    sel.value = _layout.theme;
  }

  // ── Save layout ──────────────────────────────────────────────────────────────
  async function saveLayout() {
    try {
      await API.saveOverlay(_layout);
    } catch (_) {}
  }

  // ── Mount ────────────────────────────────────────────────────────────────────
  async function mount(container) {
    _container = container;
    _selected  = null;

    container.innerHTML = `
      <div style="display:flex; height:100vh; overflow:hidden;">

        <!-- Left: gauge list -->
        <div style="width:200px; min-width:200px; background:var(--sidebar);
                    border-right:1px solid var(--border); display:flex;
                    flex-direction:column; overflow:hidden;">
          <div style="padding:12px 12px 8px; border-bottom:1px solid var(--border);">
            <div style="font-size:12px; font-weight:700; color:var(--text); margin-bottom:8px">
              Gauges
            </div>
            <button class="btn btn-sm btn-accent" id="add-gauge-btn" style="width:100%">
              + Add Gauge
            </button>
          </div>
          <div id="gauge-list" style="flex:1; overflow-y:auto;"></div>
        </div>

        <!-- Centre: preview canvas -->
        <div style="flex:1; display:flex; flex-direction:column; overflow:hidden; background:var(--bg);">

          <!-- Toolbar -->
          <div style="padding:8px 16px; border-bottom:1px solid var(--border);
                      display:flex; align-items:center; gap:8px; flex-shrink:0;">
            <span style="font-size:12px; font-weight:700; color:var(--text)">Overlay</span>
            <div style="flex:1"></div>
            <label style="font-size:10px; color:var(--text2)">Theme</label>
            <select id="theme-select" style="font-size:10px;">
              <option value="Dark">Dark</option>
              <option value="Light">Light</option>
              <option value="Colorful">Colorful</option>
              <option value="Monochrome">Monochrome</option>
            </select>
            <select id="preset-select" style="font-size:10px; max-width:120px">
              <option value="">— No Preset —</option>
            </select>
            <button class="btn btn-sm" id="save-preset-btn">Save As…</button>
            <button class="btn btn-sm btn-accent" id="save-layout-btn">Save</button>
          </div>

          <!-- 16:9 preview area wrapper -->
          <div style="flex:1; display:flex; align-items:center; justify-content:center;
                      padding:16px; overflow:hidden;">
            <div style="position:relative; max-width:100%; max-height:100%; aspect-ratio:16/9;
                        width:100%; background:#111827; border:1px solid var(--border);
                        border-radius:4px; overflow:hidden;"
                 id="preview-area">
              <!-- Gauge canvases injected here -->
              <div style="position:absolute; inset:0; display:flex; align-items:center;
                          justify-content:center; pointer-events:none; z-index:0;">
                <span style="font-size:12px; color:rgba(255,255,255,0.1); user-select:none">
                  16:9 Preview
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- Right: properties panel -->
        <div style="width:220px; min-width:220px; background:var(--sidebar);
                    border-left:1px solid var(--border); overflow-y:auto;">
          <div id="prop-panel">
            <div style="color:var(--text3); font-size:11px; padding:12px">
              Select a gauge to edit its properties.
            </div>
          </div>
        </div>

      </div>`;

    // Load config + overlay
    try {
      _layout = await API.getOverlay();
      _layout.gauges = _layout.gauges || [];
    } catch (_) {
      _layout = { is_bike: false, theme: 'Dark', gauges: [] };
    }

    await loadPresetList();
    rebuildThemeSelector();
    rebuildGaugeList();
    rebuildGaugeCanvases();
    setupMouseEvents();

    // Toolbar events
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
      // Load preset into layout
      const presets = await API.getConfig().then(c => c.presets || {});
      if (presets[name]) {
        _layout = { ...presets[name], active_preset: name };
        _selected = null;
        rebuildThemeSelector();
        rebuildGaugeList();
        rebuildGaugeCanvases();
        updatePropPanel();
      }
    });

    // Handle window resize
    const resizeObserver = new ResizeObserver(() => rebuildGaugeCanvases());
    const area = container.querySelector('#preview-area');
    if (area) resizeObserver.observe(area);
  }

  function unmount() {
    if (_animFrame) { cancelAnimationFrame(_animFrame); _animFrame = null; }
    _container = null;
  }

  Router.register('editor', { mount, unmount });
})();
