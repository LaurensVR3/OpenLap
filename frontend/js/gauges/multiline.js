/**
 * multiline.js — Multi-channel overlaid line chart with legend.
 *
 * Mirrors styles/gauge_multiline.py
 *
 * data keys: multi_channels (list of {channel, label, unit, values, value, min_val, max_val, symmetric, color_idx})
 */

const GAUGE_COLOURS = [
  '#00d4ff', '#ff6b35', '#a8ff3e', '#ff3ea8',
  '#ffd700', '#3ea8ff', '#ff3e3e', '#3effd7',
  '#c084fc', '#fb923c',
];

function _multilineColour(entry) {
  if (entry.channel === 'delta_time') {
    const v = entry.value ?? 0;
    if (v <= -0.10) return '#c084fc';
    if (v <   0.00) return '#22dd66';
    if (v <   1.00) return '#ffd700';
    return '#ff4444';
  }
  return GAUGE_COLOURS[(entry.color_idx || 0) % GAUGE_COLOURS.length];
}

const GaugeMultiline = {
  render(ctx, data, w, h) {
    const theme   = GaugeBase.getTheme(data.theme || 'Dark');
    const entries = data.multi_channels || [];

    GaugeBase.drawBackground(ctx, w, h, theme);

    if (entries.length === 0) {
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle    = theme.label;
      ctx.font         = `${Math.round(w * 0.07)}px 'Segoe UI', sans-serif`;
      ctx.fillText('No channels', w * 0.5, h * 0.5);
      return;
    }

    const sc = Math.sqrt((w / 320) * (h / 120));

    // Legend width
    const maxLabel  = Math.max(...entries.map(e => (e.label || '').length));
    const legendFrac = Math.min(0.38, Math.max(0.30, 0.05 + maxLabel * 0.022));
    const legendW   = w * legendFrac;

    // Chart area (pixels)
    const cX = w * 0.05;
    const cY = h * 0.12;
    const cW = w * (0.95 - legendFrac - 0.03) - cX;
    const cH = h * 0.76;

    const fsLeg = Math.max(8, Math.min(Math.round(8 * sc), Math.round(h * 0.09)));

    // Clip to chart area
    ctx.save();
    ctx.beginPath();
    ctx.rect(cX, cY, cW, cH);
    ctx.clip();

    // Zero line at y=0.5 (midpoint of normalised range)
    ctx.strokeStyle = 'rgba(255,255,255,0.10)';
    ctx.lineWidth   = 0.4;
    ctx.beginPath();
    ctx.moveTo(cX, cY + cH * 0.5);
    ctx.lineTo(cX + cW, cY + cH * 0.5);
    ctx.stroke();

    for (const entry of entries) {
      const vals = entry.values || [];
      const mn   = entry.min_val ?? 0;
      const mx   = entry.max_val ?? 1;
      const sym  = entry.symmetric ?? false;
      const colour = _multilineColour(entry);
      const rng  = mx !== mn ? mx - mn : 1;

      if (vals.length < 2) continue;

      // Normalise each value to [0,1]
      const norm = vals.map(v => Math.max(0, Math.min(1, (v - mn) / rng)));

      function toX(i) { return cX + cW * (i / (norm.length - 1)); }
      function toY(nv) { return cY + cH * (1 - nv); }

      // Fill under trace
      const baseline = toY(sym ? 0.5 : 0);
      ctx.beginPath();
      ctx.moveTo(toX(0), baseline);
      for (let i = 0; i < norm.length; i++) ctx.lineTo(toX(i), toY(norm[i]));
      ctx.lineTo(toX(norm.length - 1), baseline);
      ctx.closePath();
      ctx.fillStyle   = colour;
      ctx.globalAlpha = 0.08;
      ctx.fill();
      ctx.globalAlpha = 1;

      // Trace line
      ctx.beginPath();
      for (let i = 0; i < norm.length; i++) {
        const x = toX(i);
        const y = toY(norm[i]);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.strokeStyle = colour;
      ctx.lineWidth   = Math.max(1, 1.5 * sc);
      ctx.globalAlpha = 0.90;
      ctx.lineCap     = 'round';
      ctx.stroke();
      ctx.globalAlpha = 1;

      // Current value dot (rightmost point)
      const lastNorm = norm[norm.length - 1];
      ctx.beginPath();
      ctx.arc(cX + cW, toY(lastNorm), Math.max(3, 4 * sc), 0, Math.PI * 2);
      ctx.fillStyle   = colour;
      ctx.fill();
      ctx.strokeStyle = 'white';
      ctx.lineWidth   = Math.max(0.5, 0.6 * sc);
      ctx.stroke();
    }

    ctx.restore();

    // Legend (right column)
    const legendX   = w - legendW + w * 0.01;
    const nEntries  = entries.length;
    const rowH      = h * 0.80 / Math.max(nEntries, 1);
    const swatchW   = Math.round(w * 0.022);
    const swatchH   = Math.min(h * 0.05, rowH * 0.55);

    ctx.textBaseline = 'middle';
    ctx.textAlign    = 'left';

    for (let i = 0; i < entries.length; i++) {
      const entry  = entries[i];
      const colour = _multilineColour(entry);
      const label  = (entry.label || '').slice(0, 6).toUpperCase();
      const unit   = entry.unit || '';
      const value  = entry.value ?? 0;

      const yCentre = h * 0.10 + rowH * (i + 0.5);

      // Colour swatch
      ctx.fillStyle = colour;
      ctx.fillRect(legendX, yCentre - swatchH / 2, swatchW, swatchH);

      // Format value
      let valStr;
      const absV = Math.abs(value);
      if (absV >= 1000)     valStr = value.toFixed(0);
      else if (absV >= 100) valStr = value.toFixed(0);
      else if (absV >= 10)  valStr = value.toFixed(1);
      else                  valStr = value.toFixed(2);
      if (unit) valStr += '\u202f' + unit;

      // Combined label + value
      const combined = `${label.padEnd(6)}  ${valStr}`;
      ctx.fillStyle  = colour;
      ctx.font       = `bold ${fsLeg}px 'Segoe UI', sans-serif`;
      ctx.fillText(combined, legendX + swatchW + 4, yCentre);
    }
  }
};
