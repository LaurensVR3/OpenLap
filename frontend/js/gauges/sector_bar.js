/**
 * sector_bar.js — Compact sector split strip.
 *
 * Mirrors styles/gauge_sector_bar.py
 *
 * data keys: sectors (list of {num, delta, done})
 */
const GaugeSectorBar = {
  _colour(delta) {
    if (delta <= -0.10) return 'rgba(153, 38, 217, 0.95)';   // purple
    if (delta <   0.00) return 'rgba(13,  184, 71,  0.95)';  // green
    if (delta <   1.00) return 'rgba(242, 204, 13,  0.95)';  // yellow
    return                      'rgba(224, 51,  46,  0.95)';  // red
  },

  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    GaugeBase.drawBackground(ctx, w, h, theme);

    const sectors = data.sectors || [];

    if (sectors.length === 0) {
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle    = theme.label;
      ctx.font         = `${Math.max(8, Math.round(w * 0.07))}px 'Segoe UI', sans-serif`;
      ctx.fillText('No ref lap', w * 0.5, h * 0.5);
      return;
    }

    const sc = Math.sqrt((w / 320) * (h / 60));

    const done    = sectors.filter(s => s.done && s.delta != null);
    const pending = sectors.filter(s => !s.done);

    const PAD_X  = w * 0.03;
    const PAD_Y  = h * 0.12;
    const GAP    = w * 0.008;
    const TICK_W = w * 0.012;
    const boxY1  = PAD_Y;
    const boxH   = h - 2 * PAD_Y;
    const fs     = Math.max(7, Math.min(Math.round(9 * sc), Math.round(h * 0.25)));

    const tickTotal = pending.length > 0 ? pending.length * (TICK_W + GAP) : 0;
    const boxTotal  = (w - 2 * PAD_X) - tickTotal;
    const boxW      = done.length > 0
      ? Math.max(w * 0.05, (boxTotal - GAP * (done.length - 1)) / done.length)
      : 0;

    let cursor = PAD_X;
    const r = Math.max(1, Math.round(boxH * 0.18));

    // Completed sectors
    for (const s of done) {
      const col = this._colour(s.delta);

      ctx.fillStyle = col;
      ctx.beginPath();
      GaugeBase.roundRect(ctx, cursor, boxY1, boxW, boxH, r);
      ctx.fill();

      ctx.strokeStyle = 'rgba(255,255,255,0.12)';
      ctx.lineWidth   = 0.5;
      ctx.stroke();

      // Sector label (top)
      const fsSmall = Math.max(6, Math.round(fs * 0.62));
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle    = 'rgba(255,255,255,0.75)';
      ctx.font         = `${fsSmall}px 'Segoe UI', sans-serif`;
      ctx.fillText(`S${s.num ?? '?'}`, cursor + boxW / 2, boxY1 + boxH * 0.25);

      // Delta
      const sign   = s.delta >= 0 ? '+' : '';
      const dStr   = `${sign}${s.delta.toFixed(2)}`;
      ctx.fillStyle = 'white';
      ctx.font      = `bold ${fs}px 'Segoe UI', sans-serif`;
      ctx.fillText(dStr, cursor + boxW / 2, boxY1 + boxH * 0.68);

      cursor += boxW + GAP;
    }

    // Pending ticks
    const tickCol = 'rgba(89, 89, 128, 0.60)';
    const fsSmall = Math.max(6, Math.round(fs * 0.55));
    for (const s of pending) {
      ctx.fillStyle = tickCol;
      ctx.beginPath();
      GaugeBase.roundRect(ctx, cursor, boxY1 + boxH * 0.25, TICK_W, boxH * 0.50, 2);
      ctx.fill();

      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle    = 'rgba(170,170,204,0.60)';
      ctx.font         = `${fsSmall}px 'Segoe UI', sans-serif`;
      ctx.fillText(`S${s.num ?? '?'}`, cursor + TICK_W / 2, boxY1 + boxH * 0.50);

      cursor += TICK_W + GAP;
    }
  }
};
