/**
 * gmeter.js — 2D G-force meter with fading trace.
 *
 * Mirrors styles/gauge_gmeter.py
 *
 * data keys: value (gx), value_gy, history_vals (gx list), history_gy,
 *            min_val, max_val
 */
const GaugeGmeter = {
  render(ctx, data, w, h) {
    const theme  = GaugeBase.getTheme(data.theme || 'Dark');
    const gxNow  = data.value     ?? 0;
    const gyNow  = data.value_gy  ?? 0;
    const gxHist = data.history_vals || [gxNow];
    const gyHist = data.history_gy   || [gyNow];
    const gRange = data.max_val  ?? 3;

    GaugeBase.drawBackground(ctx, w, h, theme);

    // Chart margins matching Python (m_side=0.14, m_top=0.08, m_bot=0.22)
    const mSide = 0.14;
    const mTop  = 0.08;
    const mBot  = 0.22;

    // Centred square chart area
    const chartW  = w * (1 - 2 * mSide);
    const chartH  = h * (1 - mBot - mTop);
    const plotDim = Math.min(chartW, chartH);
    const cx      = w * 0.5;
    const cy      = h * mBot + chartH * 0.5;

    // Scale: plotDim pixels spans 2 * gRange * 1.20 G-units
    const scale   = plotDim / (gRange * 2 * 1.20);

    function toScreen(gx, gy) {
      // gx = longitudinal (BRAKE=+, ACCEL=-), gy = lateral (R=+, L=-)
      // In matplotlib: x=gx(lat?), y=gx(lon?)
      // From Python: gx_now is longitudinal, gy_now is lateral
      // ax.plot(gx_now, gy_now) → x=lon, y=lat
      // BUT in our display: BRAKE=top, ACCEL=bottom, R=right, L=left
      // x-axis = lateral (gy), y-axis = longitudinal (gx, positive = brake = up)
      return {
        x: cx + gy * scale,
        y: cy - gx * scale,   // negate so positive gx (brake) = up
      };
    }

    // Grid circles
    ctx.save();
    for (const r of [gRange / 3, gRange * 2 / 3, gRange]) {
      ctx.beginPath();
      ctx.arc(cx, cy, r * scale, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255,255,255,0.15)';
      ctx.lineWidth   = 0.5;
      ctx.stroke();
    }

    // Cross-hairs
    const halfSpan = gRange * 1.20 * scale;
    ctx.strokeStyle = 'rgba(255,255,255,0.20)';
    ctx.lineWidth   = 0.5;
    ctx.beginPath();
    ctx.moveTo(cx - halfSpan, cy);
    ctx.lineTo(cx + halfSpan, cy);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx, cy - halfSpan);
    ctx.lineTo(cx, cy + halfSpan);
    ctx.stroke();
    ctx.restore();

    // Ring labels
    const dim   = Math.min(w, h);
    const fsRing = Math.max(7, Math.round(dim * 0.050));
    ctx.fillStyle    = 'rgba(255,255,255,0.28)';
    ctx.font         = `${fsRing}px 'Segoe UI', sans-serif`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'bottom';
    for (const [r, lbl] of [
      [gRange / 3,     `${(gRange / 3).toFixed(0)}G`],
      [gRange * 2 / 3, `${(gRange * 2 / 3).toFixed(0)}G`],
      [gRange,         `${gRange.toFixed(0)}G`],
    ]) {
      ctx.fillText(lbl, cx, cy - r * scale - 1);
    }

    // Axis labels
    const fsAx = Math.max(8, Math.round(dim * 0.060));
    ctx.fillStyle    = theme.label;
    ctx.font         = `${fsAx}px 'Segoe UI', sans-serif`;
    ctx.textBaseline = 'bottom';
    ctx.textAlign    = 'center';
    ctx.fillText('BRAKE', cx, cy - halfSpan);
    ctx.textBaseline = 'top';
    ctx.fillText('ACCEL', cx, cy + halfSpan);
    ctx.textBaseline = 'middle';
    ctx.textAlign    = 'left';
    ctx.fillText('R', cx + halfSpan + 2, cy);
    ctx.textAlign    = 'right';
    ctx.fillText('L', cx - halfSpan - 2, cy);

    // G readout (bottom of background area)
    const fsVal = Math.max(8, Math.round(dim * 0.065));
    ctx.fillStyle    = '#ccccdd';
    ctx.font         = `${fsVal}px 'Segoe UI', sans-serif`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'bottom';
    const gxStr = (gxNow >= 0 ? '+' : '') + gxNow.toFixed(2);
    const gyStr = (gyNow >= 0 ? '+' : '') + gyNow.toFixed(2);
    ctx.fillText(`${gxStr}  /  ${gyStr}`, w * 0.5, h * 0.97);

    // History trace (fading alpha)
    const nTrace = Math.min(60, gxHist.length);
    if (nTrace >= 2) {
      const xSlice = gxHist.slice(-nTrace);
      const ySlice = gyHist.length >= nTrace
        ? gyHist.slice(-nTrace)
        : new Array(nTrace).fill(0);

      const accCol = theme.fillLo || '#4f8ef7';
      ctx.lineCap = 'round';

      for (let i = 0; i < nTrace - 1; i++) {
        const alpha = 0.05 + 0.45 * (i / (nTrace - 1));
        const p0    = toScreen(xSlice[i],     ySlice[i]);
        const p1    = toScreen(xSlice[i + 1], ySlice[i + 1]);
        ctx.beginPath();
        ctx.moveTo(p0.x, p0.y);
        ctx.lineTo(p1.x, p1.y);
        ctx.strokeStyle = accCol;
        ctx.lineWidth   = Math.max(0.8, dim * 0.006);
        ctx.globalAlpha = alpha;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // Current dot
    const gMag   = Math.min(Math.hypot(gxNow, gyNow) / gRange, 1.0);
    const dotCol = gMag > 0.80 ? theme.fillHi : (theme.fillLo || '#4f8ef7');
    const dotR   = Math.max(5, Math.round(dim * 0.045));
    const pos    = toScreen(gxNow, gyNow);

    ctx.beginPath();
    ctx.arc(pos.x, pos.y, dotR, 0, Math.PI * 2);
    ctx.fillStyle   = dotCol;
    ctx.globalAlpha = 1;
    ctx.fill();
    ctx.strokeStyle = 'white';
    ctx.lineWidth   = Math.max(0.8, dim * 0.005);
    ctx.stroke();
  }
};
