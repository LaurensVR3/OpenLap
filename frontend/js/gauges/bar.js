/**
 * bar.js — Horizontal fill bar gauge with sparkline.
 *
 * Mirrors styles/gauge_bar.py
 *
 * data keys: value, history_vals, label, unit, min_val, max_val, symmetric
 * theme keys: bg, bgEdge, track, fillPos, fillNeg, fillLo, fillHi, label, trace
 */
const GaugeBar = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    GaugeBase.drawBackground(ctx, w, h, theme);

    const value     = data.value      ?? 0;
    const hist      = data.history_vals || [value];
    const label     = (data.label     || '').toUpperCase();
    const unit      = data.unit       || '';
    const mn        = data.min_val    ?? 0;
    const mx        = data.max_val    ?? 100;
    const symmetric = data.symmetric  ?? false;

    const sc = Math.sqrt((w / 180) * (h / 120));

    const fsLabel = Math.max(8, Math.min(Math.round(10 * sc), Math.round(w * 0.08)));
    const fsVal   = Math.max(9, Math.min(Math.round(13 * sc), Math.round(w * 0.10)));

    const PAD   = w * 0.08;
    const BAR_L = PAD;
    const BAR_R = w - PAD;
    const barW  = BAR_R - BAR_L;
    const BAR_Y = h * 0.38;   // top of bar
    const BAR_H = h * 0.22;

    // Label (top zone)
    ctx.textBaseline = 'middle';
    ctx.textAlign    = 'center';
    ctx.fillStyle    = theme.label;
    ctx.font         = `${fsLabel}px 'Segoe UI', sans-serif`;
    ctx.fillText(label, w * 0.5, h * 0.89);

    // Track background for bar
    ctx.fillStyle = theme.track;
    ctx.beginPath();
    GaugeBase.roundRect(ctx, BAR_L, BAR_Y, barW, BAR_H, 2);
    ctx.fill();

    const rng = mx !== mn ? mx - mn : 1;
    let fillCol;

    if (symmetric) {
      const midX  = BAR_L + barW * (-mn / rng);
      const frac  = (value - mn) / rng;
      const fillX = BAR_L + barW * Math.max(0, Math.min(1, frac));

      if (value >= 0) {
        fillCol = theme.fillPos;
        ctx.fillStyle = fillCol;
        ctx.globalAlpha = 0.9;
        const fw = fillX - midX;
        if (fw > 0) {
          ctx.fillRect(midX, BAR_Y, fw, BAR_H);
        }
      } else {
        fillCol = theme.fillNeg;
        ctx.fillStyle = fillCol;
        ctx.globalAlpha = 0.9;
        const fw = midX - fillX;
        if (fw > 0) {
          ctx.fillRect(fillX, BAR_Y, fw, BAR_H);
        }
      }
      ctx.globalAlpha = 1;

      // Centre tick
      ctx.strokeStyle = '#3a4a5a';
      ctx.lineWidth   = 1;
      ctx.beginPath();
      ctx.moveTo(midX, BAR_Y - 2);
      ctx.lineTo(midX, BAR_Y + BAR_H + 2);
      ctx.stroke();
    } else {
      const frac  = Math.max(0, Math.min(1, (value - mn) / rng));
      fillCol = frac < 0.75 ? theme.fillLo : theme.fillHi;
      ctx.fillStyle   = fillCol;
      ctx.globalAlpha = 0.9;
      ctx.fillRect(BAR_L, BAR_Y, barW * frac, BAR_H);
      ctx.globalAlpha = 1;
    }

    // Value text (between bar and sparkline)
    const valStr = unit ? `${value.toFixed(1)} ${unit}` : value.toFixed(1);
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle    = fillCol || theme.fillLo;
    ctx.font         = `bold ${fsVal}px 'Segoe UI', sans-serif`;
    ctx.fillText(valStr, w * 0.5, h * 0.25);

    // Sparkline (bottom zone: 5%–27% of height)
    const n = Math.min(50, hist.length);
    if (n >= 2) {
      const vals = hist.slice(-n);
      const spYT = h * 0.05;
      const spYB = h * 0.27;
      const spH  = spYB - spYT;

      ctx.strokeStyle = theme.trace;
      ctx.lineWidth   = Math.max(0.6, 0.8 * sc);
      ctx.beginPath();
      for (let i = 0; i < vals.length; i++) {
        const x = BAR_L + barW * (i / (vals.length - 1));
        const y = spYT + spH * (1 - Math.max(0, Math.min(1, (vals[i] - mn) / rng)));
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }
};
