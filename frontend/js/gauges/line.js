/**
 * line.js — Area chart of channel history with value readout.
 *
 * Mirrors styles/gauge_line.py
 *
 * data keys: value, history_vals, label, unit, min_val, max_val, symmetric, channel
 * theme keys: bg, bgEdge, track, fillPos, fillNeg, fillLo, fillHi, label, unit
 */
const GaugeLine = {
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
    const channel   = data.channel    || '';

    const sc = Math.sqrt((w / 220) * (h / 100));

    const fsLabel = Math.max(8,  Math.min(Math.round(9  * sc), Math.round(w * 0.07)));
    const fsVal   = Math.max(9,  Math.min(Math.round(14 * sc), Math.round(w * 0.11)));
    const fsUnit  = Math.max(7,  Math.min(Math.round(7  * sc), Math.round(w * 0.06)));

    // Chart area: left 68% of width, middle 62% of height
    const cX = w * 0.04;
    const cY = h * 0.20;
    const cW = w * 0.68;
    const cH = h * 0.62;

    const rng = mx !== mn ? mx - mn : 1;
    const pad = rng * 0.08;
    const yMin = mn - pad;
    const yMax = mx + pad;
    const yRng = yMax - yMin;

    // Line colour
    let lineCol;
    if (symmetric) {
      lineCol = value >= 0 ? theme.fillPos : theme.fillNeg;
    } else {
      const frac = Math.max(0, Math.min(1, (value - mn) / rng));
      lineCol = frac > 0.80 ? theme.fillHi : theme.fillLo;
    }

    // Clip to chart area
    ctx.save();
    ctx.beginPath();
    ctx.rect(cX, cY, cW, cH);
    ctx.clip();

    const n    = Math.min(120, hist.length);
    const vals = hist.slice(-n);

    function toScreenX(i) { return cX + cW * (i / Math.max(1, vals.length - 1)); }
    function toScreenY(v) {
      return cY + cH * (1 - Math.max(0, Math.min(1, (v - yMin) / yRng)));
    }

    // Zero line for symmetric channels
    if (symmetric) {
      const y0 = toScreenY(0);
      ctx.strokeStyle = theme.track;
      ctx.lineWidth   = 0.8;
      ctx.beginPath();
      ctx.moveTo(cX, y0);
      ctx.lineTo(cX + cW, y0);
      ctx.stroke();
    }

    if (vals.length >= 2) {
      // Fill under the trace
      const baseline = toScreenY(symmetric ? 0 : mn);

      ctx.beginPath();
      ctx.moveTo(toScreenX(0), baseline);
      for (let i = 0; i < vals.length; i++) {
        ctx.lineTo(toScreenX(i), toScreenY(vals[i]));
      }
      ctx.lineTo(toScreenX(vals.length - 1), baseline);
      ctx.closePath();
      ctx.fillStyle   = lineCol;
      ctx.globalAlpha = 0.18;
      ctx.fill();
      ctx.globalAlpha = 1;

      // Trace line
      ctx.beginPath();
      for (let i = 0; i < vals.length; i++) {
        const x = toScreenX(i);
        const y = toScreenY(vals[i]);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.strokeStyle = lineCol;
      ctx.lineWidth   = Math.max(1, 1.4 * sc);
      ctx.lineCap     = 'round';
      ctx.stroke();
    }

    ctx.restore();

    // Label (top-left)
    ctx.textBaseline = 'top';
    ctx.textAlign    = 'left';
    ctx.fillStyle    = theme.label;
    ctx.font         = `${fsLabel}px 'Segoe UI', sans-serif`;
    ctx.fillText(label, w * 0.04, h * 0.05);

    // Value readout (right panel, upper half)
    const valStr = GaugeBase.fmtValue(value, channel);
    ctx.textBaseline = 'middle';
    ctx.textAlign    = 'right';
    ctx.fillStyle    = lineCol;
    ctx.font         = `bold ${fsVal}px 'Segoe UI', sans-serif`;
    ctx.fillText(valStr, w * 0.97, h * 0.44);

    if (unit) {
      ctx.fillStyle = theme.unit;
      ctx.font      = `${fsUnit}px 'Segoe UI', sans-serif`;
      ctx.fillText(unit, w * 0.97, h * 0.72);
    }
  }
};
