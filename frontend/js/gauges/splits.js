/**
 * splits.js — Sector split comparison table.
 *
 * Mirrors styles/gauge_splits.py
 *
 * data keys: value (cur elapsed), sectors (list of {num, ref_t, cur_t, delta, done, boundary_elapsed})
 * theme keys: bg, bgEdge, label, text
 */
const GaugeSplits = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    GaugeBase.drawBackground(ctx, w, h, theme);

    const sectors = data.sectors || [];
    const sc = Math.sqrt((w / 160) * (h / 200));

    const fsTitle = Math.max(8, Math.min(Math.round(8 * sc), Math.round(w * 0.09)));
    const fsRow   = Math.max(8, Math.min(Math.round(9 * sc), Math.round(w * 0.09)));
    const fsHdr   = Math.max(7, Math.min(Math.round(6 * sc), Math.round(w * 0.075)));

    // Title
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle    = theme.label;
    ctx.font         = `${fsTitle}px 'Segoe UI', sans-serif`;
    ctx.fillText('SPLITS', w * 0.5, h * 0.07);

    if (sectors.length === 0) {
      ctx.fillStyle = theme.label;
      ctx.font      = `${fsRow}px 'Segoe UI', sans-serif`;
      ctx.fillText('No ref lap', w * 0.5, h * 0.5);
      return;
    }

    const n    = sectors.length;
    const yTop = h * 0.84;
    const rowH = (yTop - h * 0.10) / (n + 1);  // +1 for header

    // Column x positions (fractions of w)
    const COL_S    = w * 0.12;
    const COL_REF  = w * 0.38;
    const COL_CUR  = w * 0.62;
    const COL_DIFF = w * 0.87;

    // Header
    const yHdr = yTop - rowH * 0.45;
    ctx.fillStyle    = theme.label;
    ctx.font         = `${fsHdr}px 'Segoe UI', sans-serif`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    for (const [x, txt] of [[COL_S, 'S'], [COL_REF, 'REF'], [COL_CUR, 'CUR'], [COL_DIFF, 'DIFF']]) {
      ctx.fillText(txt, x, yHdr);
    }

    // Find next incomplete sector
    let nextIncomplete = null;
    for (let i = 0; i < sectors.length; i++) {
      if (!sectors[i].done) { nextIncomplete = i; break; }
    }

    for (let i = 0; i < sectors.length; i++) {
      const s    = sectors[i];
      const y    = yTop - (i + 1) * rowH;
      const rowY = y + rowH * 0.5;

      // Highlight current sector
      if (i === nextIncomplete) {
        ctx.fillStyle   = 'rgba(255,255,255,0.07)';
        ctx.fillRect(w * 0.05, y + 1, w * 0.90, rowH - 2);
      }

      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.font         = `${fsRow}px 'Segoe UI', sans-serif`;

      // Sector number
      ctx.fillStyle = theme.text;
      ctx.fillText(`S${s.num ?? (i + 1)}`, COL_S, rowY);

      // Ref time
      ctx.fillStyle = '#888888';
      ctx.fillText(s.ref_t != null ? s.ref_t.toFixed(2) : '\u2014', COL_REF, rowY);

      if (s.cur_t != null) {
        // Current time
        ctx.fillStyle = theme.text;
        ctx.fillText(s.cur_t.toFixed(2), COL_CUR, rowY);

        // Delta
        if (s.delta != null) {
          let dCol;
          if (Math.abs(s.delta) < 0.01)  dCol = '#e8e8e8';
          else if (s.delta < 0)           dCol = '#22dd66';
          else                            dCol = '#ff4444';
          ctx.fillStyle = dCol;
          ctx.font      = `bold ${fsRow}px 'Segoe UI', sans-serif`;
          ctx.fillText((s.delta >= 0 ? '+' : '') + s.delta.toFixed(2), COL_DIFF, rowY);
          ctx.font      = `${fsRow}px 'Segoe UI', sans-serif`;
        }
      } else {
        ctx.fillStyle = '#444444';
        ctx.fillText('\u2014', COL_CUR, rowY);
        ctx.fillText('\u2014', COL_DIFF, rowY);
      }
    }
  }
};
