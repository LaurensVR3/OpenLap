/**
 * compare.js — Dual-trace line chart: current lap vs reference lap.
 *
 * Mirrors styles/gauge_compare.py
 *
 * data keys: value, history_vals, ref_history_vals, label, unit,
 *            min_val, max_val, symmetric, channel
 * theme keys: bg, bgEdge, fillPos, fillNeg, fillLo, fillHi, label, unit, track
 */
const GaugeCompare = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    GaugeBase.drawBackground(ctx, w, h, theme);

    const value     = data.value          ?? 0;
    const hist      = data.history_vals   || [value];
    const refHist   = data.ref_history_vals || [];
    const label     = (data.label         || '').toUpperCase();
    const unit      = data.unit           || '';
    const mn        = data.min_val        ?? 0;
    const mx        = data.max_val        ?? 100;
    const symmetric = data.symmetric      ?? false;
    const channel   = data.channel        || '';

    const sc = Math.sqrt((w / 220) * (h / 100));

    const fsLabel = Math.max(8,  Math.min(Math.round(9  * sc), Math.round(w * 0.07)));
    const fsVal   = Math.max(9,  Math.min(Math.round(13 * sc), Math.round(w * 0.10)));
    const fsUnit  = Math.max(7,  Math.min(Math.round(7  * sc), Math.round(w * 0.06)));
    const fsLeg   = Math.max(7,  Math.min(Math.round(6  * sc), Math.round(w * 0.055)));

    const rng = mx !== mn ? mx - mn : 1;

    let lineCol;
    if (symmetric) {
      lineCol = value >= 0 ? theme.fillPos : theme.fillNeg;
    } else {
      const frac = Math.max(0, Math.min(1, (value - mn) / rng));
      lineCol = frac > 0.80 ? theme.fillHi : theme.fillLo;
    }

    const refCol = '#999999';

    // Chart area
    const cX = w * 0.04;
    const cY = h * 0.20;
    const cW = w * 0.70;
    const cH = h * 0.62;

    const pad  = rng * 0.10;
    const yMin = mn - pad;
    const yMax = mx + pad;
    const yRng = yMax - yMin;

    ctx.save();
    ctx.beginPath();
    ctx.rect(cX, cY, cW, cH);
    ctx.clip();

    const n       = Math.min(120, hist.length);
    const curVals = hist.slice(-n);

    function toX(i) { return cX + cW * (i / Math.max(1, curVals.length - 1)); }
    function toY(v) { return cY + cH * (1 - Math.max(0, Math.min(1, (v - yMin) / yRng))); }

    if (symmetric) {
      const y0 = toY(0);
      ctx.strokeStyle = theme.track;
      ctx.lineWidth   = 0.6;
      ctx.beginPath();
      ctx.moveTo(cX, y0);
      ctx.lineTo(cX + cW, y0);
      ctx.stroke();
    }

    // Reference trace (dashed grey)
    const hasRef = refHist.length >= 2;
    if (hasRef) {
      let refVals = refHist.slice(-n);
      // Align lengths
      while (refVals.length < curVals.length) refVals.unshift(refVals[0]);
      if (refVals.length > curVals.length) refVals = refVals.slice(-curVals.length);

      ctx.strokeStyle = refCol;
      ctx.lineWidth   = Math.max(0.8, sc);
      ctx.globalAlpha = 0.70;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      for (let i = 0; i < refVals.length; i++) {
        const x = toX(i);
        const y = toY(refVals[i]);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

    // Current trace + fill
    if (curVals.length >= 2) {
      const baseline = toY(symmetric ? 0 : mn);

      ctx.beginPath();
      ctx.moveTo(toX(0), baseline);
      for (let i = 0; i < curVals.length; i++) {
        ctx.lineTo(toX(i), toY(curVals[i]));
      }
      ctx.lineTo(toX(curVals.length - 1), baseline);
      ctx.closePath();
      ctx.fillStyle   = lineCol;
      ctx.globalAlpha = 0.12;
      ctx.fill();
      ctx.globalAlpha = 1;

      ctx.beginPath();
      for (let i = 0; i < curVals.length; i++) {
        const x = toX(i);
        const y = toY(curVals[i]);
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

    // Value (right panel, top)
    const valStr = GaugeBase.fmtValue(value, channel);
    ctx.textBaseline = 'top';
    ctx.textAlign    = 'right';
    ctx.fillStyle    = lineCol;
    ctx.font         = `bold ${fsVal}px 'Segoe UI', sans-serif`;
    ctx.fillText(valStr, w * 0.97, h * 0.08);

    if (unit) {
      ctx.fillStyle = theme.unit;
      ctx.font      = `${fsUnit}px 'Segoe UI', sans-serif`;
      ctx.fillText(unit, w * 0.97, h * 0.30);
    }

    // Legend
    if (hasRef) {
      ctx.textBaseline = 'middle';
      ctx.fillStyle    = lineCol;
      ctx.font         = `${fsLeg}px 'Segoe UI', sans-serif`;
      ctx.fillText('\u2014 NOW', w * 0.97, h * 0.72);

      ctx.fillStyle = refCol;
      ctx.fillText('-- REF', w * 0.97, h * 0.87);
    }
  }
};
