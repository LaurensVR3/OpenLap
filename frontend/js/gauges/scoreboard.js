/**
 * scoreboard.js — Lap scoreboard panel.
 *
 * Mirrors styles/gauge_lap_scoreboard.py
 *
 * data keys: lap_num, total_laps, lap_elapsed, best_so_far (number | null)
 * theme keys: bg, bgEdge, text, label, fillPos, fillLo, fillHi
 */
const GaugeScoreboard = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    GaugeBase.drawBackground(ctx, w, h, theme);
    GaugeBase.drawAccentBar(ctx, w, h, theme.fillPos);

    const lapNum    = Math.max(0, Math.round(data.lap_num    ?? 1));
    const totalLaps = Math.max(1, Math.round(data.total_laps ?? 1));
    const elapsed   = data.lap_elapsed ?? 0;
    const best      = data.best_so_far;   // number or null/undefined

    // Delta against best
    let deltaTxt, deltaCol;
    if (best != null && best > 0) {
      const delta = elapsed - best;
      deltaTxt = (delta >= 0 ? '+' : '') + delta.toFixed(3);
      deltaCol = delta < 0 ? theme.fillLo : theme.fillHi;
    } else {
      deltaTxt = '—';
      deltaCol = theme.label;
    }

    const isOutlap = lapNum === 0;
    const lapLabel = isOutlap ? 'OUT LAP' : 'LAP';
    const lapVal   = isOutlap ? '—' : `${lapNum} / ${totalLaps}`;

    const rows = [
      [lapLabel, lapVal,                          theme.text],
      ['BEST',   best != null ? GaugeBase.fmtTime(best) : '—', theme.label],
      ['CURRENT', GaugeBase.fmtTime(elapsed),    theme.text],
      ['DELTA',   deltaTxt,                       deltaCol],
    ];

    const n       = rows.length;
    const yTop    = h * 0.94;
    const yBottom = h * 0.06;
    const rowH    = (yTop - yBottom) / n;

    // Font sizes matching gauge_lap_scoreboard.py formula:
    // fs_label = max(5, int(h * row_h * 0.28 / 1.39))
    // row_h = 1/n (fraction), so row height in px = h/n
    const rowPx   = h / n;
    const fsLabel = Math.max(8,  Math.round(rowPx * 0.28));
    const fsValue = Math.max(10, Math.round(rowPx * 0.52));

    const padL = w * 0.08;

    // Thin horizontal dividers between rows
    ctx.strokeStyle = theme.bgEdge;
    ctx.lineWidth   = 0.5;
    for (let i = 1; i < n; i++) {
      const yDiv = yTop - rowH * i;
      ctx.beginPath();
      ctx.moveTo(w * 0.06, yDiv);
      ctx.lineTo(w * 0.97, yDiv);
      ctx.stroke();
    }

    ctx.textBaseline = 'middle';

    for (let i = 0; i < rows.length; i++) {
      const [lbl, val, col] = rows[i];
      const yc   = yTop - rowH * (i + 0.5);
      const yLbl = yc + rowH * 0.20;
      const yVal = yc - rowH * 0.12;

      // Label (left-aligned)
      ctx.textAlign = 'left';
      ctx.fillStyle = theme.label;
      ctx.font      = `${fsLabel}px 'Segoe UI', sans-serif`;
      ctx.fillText(lbl, padL, yLbl);

      // Value (right-aligned, monospace)
      ctx.textAlign  = 'right';
      ctx.fillStyle  = col;
      ctx.font       = `bold ${fsValue}px 'Consolas', monospace`;
      ctx.fillText(val, w * 0.97, yVal);
    }
  }
};
