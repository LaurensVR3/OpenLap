/**
 * delta.js — Delta time gauge (current vs reference lap).
 *
 * Mirrors styles/gauge_delta.py
 *
 * data keys: value (delta_seconds), history_vals, label
 * theme keys: bg, bgEdge, label
 */
const GaugeDelta = {
  _NEUTRAL_BAND: 0.10,

  _colour(delta) {
    if (Math.abs(delta) <= this._NEUTRAL_BAND) return '#e8e8e8';
    return delta < 0 ? '#22dd66' : '#ff4444';
  },

  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    GaugeBase.drawBackground(ctx, w, h, theme);

    const value   = data.value ?? 0;
    const history = data.history_vals || [0];
    const label   = (data.label || 'Delta').toUpperCase();

    const sc = Math.sqrt((w / 120) * (h / 160));

    const fsLabel = Math.max(8, Math.min(Math.round(10 * sc), Math.round(w * 0.12)));
    const fsValue = Math.max(12, Math.min(Math.round(28 * sc), Math.round(w * 0.16)));

    const colour = this._colour(value);

    // Sign + value text
    const txt = (value >= 0 ? '+' : '\u2212') + Math.abs(value).toFixed(3);

    ctx.textBaseline = 'middle';
    ctx.textAlign    = 'center';

    // Label
    ctx.fillStyle = theme.label;
    ctx.font      = `${fsLabel}px 'Segoe UI', sans-serif`;
    ctx.fillText(label, w * 0.5, h * 0.20);

    // Value
    ctx.fillStyle  = colour;
    ctx.font       = `bold ${fsValue}px 'Segoe UI', sans-serif`;
    ctx.fillText(txt, w * 0.5, h * 0.54);

    // Sparkline (bottom 18% of height)
    const n = Math.min(150, history.length);
    if (n >= 2) {
      const vals   = history.slice(-n);
      const spX    = w * 0.08;
      const spY    = h * 0.75;
      const spW    = w * 0.84;
      const spH    = h * 0.18;

      // Determine y range from data
      let lo = Math.min(...vals);
      let hi = Math.max(...vals);
      if (lo === hi) { lo -= 1; hi += 1; }
      const rng = hi - lo;

      function toX(i) { return spX + spW * (i / (vals.length - 1)); }
      function toY(v) { return spY + spH * (1 - Math.max(0, Math.min(1, (v - lo) / rng))); }

      // Zero line
      const y0 = toY(0);
      ctx.strokeStyle = 'rgba(255,255,255,0.25)';
      ctx.lineWidth   = 0.5;
      ctx.beginPath();
      ctx.moveTo(spX, y0);
      ctx.lineTo(spX + spW, y0);
      ctx.stroke();

      // Fill positive (slower) = red
      ctx.beginPath();
      ctx.moveTo(toX(0), Math.min(toY(vals[0]), y0));
      for (let i = 0; i < vals.length; i++) {
        ctx.lineTo(toX(i), toY(vals[i]));
      }
      ctx.lineTo(toX(vals.length - 1), y0);
      ctx.closePath();
      ctx.fillStyle   = '#ff4444';
      ctx.globalAlpha = 0.35;
      ctx.fill();
      ctx.globalAlpha = 1;

      // Fill negative (faster) = green
      ctx.beginPath();
      ctx.moveTo(toX(0), Math.max(toY(vals[0]), y0));
      for (let i = 0; i < vals.length; i++) {
        ctx.lineTo(toX(i), toY(vals[i]));
      }
      ctx.lineTo(toX(vals.length - 1), y0);
      ctx.closePath();
      ctx.fillStyle   = '#22dd66';
      ctx.globalAlpha = 0.35;
      ctx.fill();
      ctx.globalAlpha = 1;

      // Trend line
      ctx.strokeStyle = colour;
      ctx.lineWidth   = 0.8;
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      for (let i = 0; i < vals.length; i++) {
        const x = toX(i);
        const y = toY(vals[i]);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }
};
