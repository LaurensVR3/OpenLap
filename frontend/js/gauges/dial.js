/**
 * dial.js — Circular arc gauge with needle.
 *
 * Mirrors styles/gauge_dial.py
 *
 * Arc spans 240° clockwise from 210° to 330° (bottom-left to bottom-right).
 * Symmetric: zero at top (90°).
 *
 * data keys: value, label, unit, min_val, max_val, symmetric, channel
 */
const GaugeDial = {
  render(ctx, data, w, h) {
    const theme     = GaugeBase.getTheme(data.theme || 'Dark');
    const value     = data.value     ?? 0;
    const label     = (data.label    || '').toUpperCase();
    const unit      = data.unit      || '';
    const mn        = data.min_val   ?? 0;
    const mx        = data.max_val   ?? 100;
    const symmetric = data.symmetric ?? false;
    const channel   = data.channel   || '';

    const sc = Math.sqrt((w / 160) * (h / 160));

    // Centre + radius — use the smaller dimension, leave some padding
    const dim  = Math.min(w, h);
    const cx   = w / 2;
    const cy   = h / 2;
    const R    = dim * 0.5 * 0.98;   // fills most of the cell

    // Background circle
    ctx.beginPath();
    ctx.arc(cx, cy, R * 1.00, 0, Math.PI * 2);
    ctx.fillStyle = theme.bg;
    ctx.fill();
    ctx.strokeStyle = theme.bgEdge;
    ctx.lineWidth   = 1;
    ctx.stroke();

    const ARC_START_DEG = 210;     // degrees (math convention: 0=right, CCW positive)
    const ARC_SWEEP_DEG = 240;     // total sweep clockwise
    const ARC_END_DEG   = ARC_START_DEG - ARC_SWEEP_DEG;  // = -30 = 330

    // Convert display degrees (where 0=right, CCW positive in math but we want
    // clockwise in display): Canvas uses radians where 0=right, CW positive
    function toRad(deg) {
      // Canvas: 0 = right, CW positive (i.e. +y is down)
      // Math convention to Canvas: negate, then convert
      return -deg * Math.PI / 180;
    }

    const R_TRACK  = R * 0.72;
    const LW_TRACK = Math.max(4, Math.round(10 * sc));

    const rng  = mx !== mn ? mx - mn : 1;
    const frac = Math.max(0, Math.min(1, (value - mn) / rng));

    // Track arc (full, from ARC_START_DEG to ARC_END_DEG clockwise)
    // In Canvas terms: start = toRad(ARC_START_DEG), end = toRad(ARC_END_DEG),
    // anticlockwise=true (because our toRad negates, making it CW on screen)
    ctx.beginPath();
    ctx.arc(cx, cy, R_TRACK, toRad(ARC_START_DEG), toRad(ARC_END_DEG), false);
    ctx.strokeStyle = theme.track;
    ctx.lineWidth   = LW_TRACK;
    ctx.lineCap     = 'round';
    ctx.stroke();

    // Fill arc
    let fillCol, needleAngleDeg;
    if (symmetric) {
      // Zero at 90°, fill from 90° toward ARC_START or ARC_END
      const zeroAngleDeg = 90;
      needleAngleDeg = zeroAngleDeg - ARC_SWEEP_DEG * (frac - 0.5);
      fillCol = value >= 0 ? theme.fillPos : theme.fillNeg;

      if (Math.abs(frac - 0.5) > 0.001) {
        ctx.beginPath();
        ctx.arc(cx, cy, R_TRACK, toRad(zeroAngleDeg), toRad(needleAngleDeg), false);
        ctx.strokeStyle = fillCol;
        ctx.lineWidth   = LW_TRACK;
        ctx.lineCap     = 'round';
        ctx.stroke();
      }
    } else {
      needleAngleDeg = ARC_START_DEG - ARC_SWEEP_DEG * frac;
      fillCol = frac < 0.80 ? theme.fillLo : theme.fillHi;

      if (frac > 0.001) {
        ctx.beginPath();
        ctx.arc(cx, cy, R_TRACK, toRad(ARC_START_DEG), toRad(needleAngleDeg), false);
        ctx.strokeStyle = fillCol;
        ctx.lineWidth   = LW_TRACK;
        ctx.lineCap     = 'round';
        ctx.stroke();
      }
    }

    // Tick marks at 0%, 25%, 50%, 75%, 100%
    for (const tf of [0, 0.25, 0.5, 0.75, 1.0]) {
      const taDeg = ARC_START_DEG - ARC_SWEEP_DEG * tf;
      const taRad = toRad(taDeg);
      const r0    = R * 0.62;
      const r1    = R * 0.70;
      ctx.beginPath();
      ctx.moveTo(cx + r0 * Math.cos(taRad), cy + r0 * Math.sin(taRad));
      ctx.lineTo(cx + r1 * Math.cos(taRad), cy + r1 * Math.sin(taRad));
      ctx.strokeStyle = '#2a3a4a';
      ctx.lineWidth   = Math.max(0.8, sc);
      ctx.stroke();
    }

    // Needle
    const naRad   = toRad(needleAngleDeg);
    const needleR = R * 0.60;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + needleR * Math.cos(naRad), cy + needleR * Math.sin(naRad));
    ctx.strokeStyle = theme.text;
    ctx.lineWidth   = Math.max(1, 1.5 * sc);
    ctx.lineCap     = 'round';
    ctx.stroke();

    // Centre dot
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(3, 5 * sc), 0, Math.PI * 2);
    ctx.fillStyle = theme.text;
    ctx.fill();

    // Value text (slightly below centre)
    const textBudget = Math.round(Math.min(w, h) * 0.35);
    const fsValue = Math.max(8, Math.min(Math.round(22 * sc), Math.round(textBudget * 0.72)));
    const fsLabel = Math.max(7, Math.min(Math.round(9 * sc), Math.round(w * 0.08)));
    const fsUnit  = Math.max(6, Math.min(Math.round(7 * sc), Math.round(w * 0.06)));

    const valStr = GaugeBase.fmtValue(value, channel);

    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';

    ctx.fillStyle  = theme.text;
    ctx.font       = `bold ${fsValue}px 'Segoe UI', sans-serif`;
    ctx.fillText(valStr, cx, cy + R * 0.15);

    ctx.fillStyle = theme.unit;
    ctx.font      = `${fsUnit}px 'Segoe UI', sans-serif`;
    ctx.fillText(unit, cx, cy + R * 0.60);

    ctx.fillStyle = theme.label;
    ctx.font      = `${fsLabel}px 'Segoe UI', sans-serif`;
    ctx.fillText(label, cx, cy - R * 0.46);
  }
};
