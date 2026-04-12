/**
 * base.js — Shared Canvas drawing utilities for all gauge styles.
 *
 * All gauge render functions share this signature:
 *   render(ctx, data, w, h)
 *
 * Where:
 *   ctx  — CanvasRenderingContext2D already sized to (w, h)
 *   data — JS object matching the data dict passed by overlay_worker.py
 *   w, h — pixel dimensions of this gauge's canvas region
 */

// ── Theme colours (mirrors overlay_themes.py) ─────────────────────────────────
const THEMES = {
  Dark: {
    bg:       'rgba(13, 18, 31, 0.74)',
    bgEdge:   'rgba(255,255,255,0.09)',
    track:    '#1c2c3a',
    fillPos:  '#ff9f00',
    fillNeg:  '#3dabff',
    fillLo:   '#00d4ff',
    fillHi:   '#ff4422',
    text:     '#f0f4f8',
    label:    '#4e6578',
    unit:     '#5d7ea0',
    trace:    '#2a3d50',
    ground:   '#2a3a4a',
    leanSafe:   '#00cc66',
    leanWarn:   '#ffaa00',
    leanDanger: '#ff3333',
  },
  Light: {
    bg:       'rgba(245, 247, 252, 0.88)',
    bgEdge:   'rgba(0,0,0,0.08)',
    track:    '#c8d4e0',
    fillPos:  '#cc5500',
    fillNeg:  '#0055cc',
    fillLo:   '#006699',
    fillHi:   '#cc1100',
    text:     '#111111',
    label:    '#778899',
    unit:     '#4466aa',
    trace:    '#99aabb',
    ground:   '#99aabb',
    leanSafe:   '#009944',
    leanWarn:   '#cc6600',
    leanDanger: '#cc1100',
  },
  Colorful: {
    bg:       'rgba(10, 3, 31, 0.88)',
    bgEdge:   'rgba(140, 56, 255, 0.35)',
    track:    '#1a0535',
    fillPos:  '#ff3399',
    fillNeg:  '#00ff99',
    fillLo:   '#ff9900',
    fillHi:   '#ff0033',
    text:     '#ffffff',
    label:    '#aa66ee',
    unit:     '#cc88ff',
    trace:    '#440066',
    ground:   '#440066',
    leanSafe:   '#00ff99',
    leanWarn:   '#ff9900',
    leanDanger: '#ff0033',
  },
  Monochrome: {
    bg:       'rgba(0,0,0,0.80)',
    bgEdge:   'rgba(255,255,255,0.18)',
    track:    '#282828',
    fillPos:  '#ffffff',
    fillNeg:  '#aaaaaa',
    fillLo:   '#ffffff',
    fillHi:   '#ffffff',
    text:     '#ffffff',
    label:    '#666666',
    unit:     '#888888',
    trace:    '#333333',
    ground:   '#333333',
    leanSafe:   '#cccccc',
    leanWarn:   '#ffffff',
    leanDanger: '#ffffff',
  },
};

/**
 * Get theme object by name, falling back to Dark.
 * @param {string} name
 * @returns {object}
 */
function getTheme(name) {
  return THEMES[name] || THEMES.Dark;
}

// ── Canvas helpers ─────────────────────────────────────────────────────────────

/**
 * Draw a rounded rectangle path. Polyfill for ctx.roundRect() which
 * is not available in older WebKits (Safari < 16.4).
 */
function roundRect(ctx, x, y, w, h, r) {
  if (typeof ctx.roundRect === 'function') {
    ctx.roundRect(x, y, w, h, r);
    return;
  }
  const clampedR = Math.min(r, w / 2, h / 2);
  ctx.moveTo(x + clampedR, y);
  ctx.arcTo(x + w, y,     x + w, y + h, clampedR);
  ctx.arcTo(x + w, y + h, x,     y + h, clampedR);
  ctx.arcTo(x,     y + h, x,     y,     clampedR);
  ctx.arcTo(x,     y,     x + w, y,     clampedR);
  ctx.closePath();
}

/**
 * Fill a rounded pill background with a thin border.
 * Mirrors the FancyBboxPatch used in all matplotlib gauges.
 */
function drawBackground(ctx, w, h, theme) {
  const pad = Math.max(2, Math.round(w * 0.02));
  const r   = Math.max(4, Math.round(Math.min(w, h) * 0.06));

  ctx.beginPath();
  roundRect(ctx, pad, pad, w - pad * 2, h - pad * 2, r);
  ctx.fillStyle = theme.bg;
  ctx.fill();

  ctx.beginPath();
  roundRect(ctx, pad, pad, w - pad * 2, h - pad * 2, r);
  ctx.strokeStyle = theme.bgEdge;
  ctx.lineWidth   = 1;
  ctx.stroke();
}

/**
 * Draw the thin vertical accent bar on the left edge.
 * Mirrors the ax.plot([0.035, 0.035], [0.08, 0.92]) pattern.
 */
function drawAccentBar(ctx, w, h, color) {
  const x    = Math.round(w * 0.035);
  const y1   = Math.round(h * 0.08);
  const y2   = Math.round(h * 0.92);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth   = Math.max(1.5, w * 0.025);
  ctx.lineCap     = 'round';
  ctx.beginPath();
  ctx.moveTo(x, y1);
  ctx.lineTo(x, y2);
  ctx.stroke();
  ctx.restore();
}

/**
 * Scale font size proportionally to gauge dimensions.
 * Mirrors scale_factor() from overlay_utils.py.
 *
 * @param {number} baseSize   — reference font size at base dimensions
 * @param {number} w          — actual width
 * @param {number} h          — actual height
 * @param {number} baseW      — reference width  (default 120)
 * @param {number} baseH      — reference height (default 160)
 * @returns {number} pixel font size (integer, clamped to a minimum)
 */
function scaleFont(baseSize, w, h, baseW = 120, baseH = 160) {
  const sc = Math.sqrt((w / baseW) * (h / baseH));
  return Math.max(8, Math.round(baseSize * sc));
}

/**
 * Format a numeric gauge value to a display string.
 * Mirrors the formatting logic in gauge_numeric.py.
 *
 * @param {number} value
 * @param {string} channel
 * @returns {string}
 */
function fmtValue(value, channel) {
  if (channel === 'lap_time') {
    if (value == null || value < 0) return '—';
    const m = Math.floor(value / 60);
    const s = (value % 60).toFixed(3).padStart(6, '0');
    return value >= 60 ? `${m}:${s}` : value.toFixed(3);
  }
  if (channel === 'lean') return value.toFixed(1);
  if (channel === 'delta_time') {
    if (value == null) return '—';
    return (value >= 0 ? '+' : '') + value.toFixed(3);
  }
  const abs = Math.abs(value);
  if (abs >= 10000) return value.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  if (abs >= 100)   return value.toFixed(0);
  if (abs >= 10)    return value.toFixed(1);
  return value.toFixed(2);
}

/**
 * Format a lap time as M:SS.mmm (for scoreboard / splits).
 */
function fmtTime(secs) {
  if (secs == null || secs < 0) return '—';
  const m = Math.floor(secs / 60);
  const s = (secs % 60).toFixed(3).padStart(6, '0');
  return `${m}:${s}`;
}

// Export as a namespace object (no ES module build step required)
const GaugeBase = { getTheme, roundRect, drawBackground, drawAccentBar, scaleFont, fmtValue, fmtTime, THEMES };
