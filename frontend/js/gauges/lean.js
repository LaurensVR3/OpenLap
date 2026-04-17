/**
 * lean.js — Motorcycle lean angle visualisation.
 *
 * Mirrors styles/gauge_lean.py
 *
 * data keys: value (degrees, positive = right), label, unit, min_val, max_val
 */
const GaugeLean = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    const value  = Math.max(-90, Math.min(90, data.value ?? 0));
    const label  = (data.label || 'Lean').toUpperCase();
    const unit   = data.unit || '°';

    const sc  = Math.sqrt((w / 140) * (h / 180));
    const dim = Math.min(w, h);

    // Background circle (same as matplotlib: Circle with radius=0.98 of axis)
    const cx = w * 0.5;
    const cy = h * 0.5;
    const R  = Math.min(w, h) * 0.5 * 0.96;

    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.fillStyle = theme.bg;
    ctx.fill();
    ctx.strokeStyle = theme.bgEdge;
    ctx.lineWidth   = 1;
    ctx.stroke();

    // Lean colour
    const absLean = Math.abs(value);
    let leanCol;
    if (absLean < 20)       leanCol = theme.leanSafe;
    else if (absLean < 40)  leanCol = theme.leanWarn;
    else                    leanCol = theme.leanDanger;

    // Ground line (fixed at bottom of circle)
    const groundY = cy + R * 0.72;
    ctx.save();
    ctx.strokeStyle = theme.ground || '#2a3a4a';
    ctx.lineWidth   = Math.max(0.8, 1.2 * sc);
    ctx.beginPath();
    ctx.moveTo(cx - R * 0.85, groundY);
    ctx.lineTo(cx + R * 0.85, groundY);
    ctx.stroke();

    // Rotate canvas for bike silhouette
    const leanRad = value * Math.PI / 180;  // positive=right lean (normalized in data loaders)

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(leanRad);

    // ── Bike body (vertical bar from -0.30 to +0.28 in normalised coords)
    const bScale = R * 0.85;  // convert normalised [-1,1] to pixels

    ctx.strokeStyle = theme.bike_body || 'white';
    ctx.lineWidth   = Math.max(2.5, 4.0 * sc);
    ctx.lineCap     = 'round';
    ctx.beginPath();
    ctx.moveTo(-0.04 * bScale, -(-0.30) * bScale);   // flip y: matplotlib y-up, canvas y-down
    ctx.lineTo(-0.04 * bScale, -(0.28) * bScale);
    ctx.stroke();

    // ── Wheels (normalised positions from Python code)
    const bikePartsCol = theme.bike_parts || '#aabbcc';

    for (const [nx, ny, nr] of [
      [0.18,  -0.38, 0.20 * sc * 0.6],
      [-0.22, -0.30, 0.19 * sc * 0.6],
    ]) {
      ctx.beginPath();
      ctx.arc(nx * bScale, -ny * bScale, nr * bScale, 0, Math.PI * 2);
      ctx.strokeStyle = bikePartsCol;
      ctx.lineWidth   = Math.max(1.5, 2.5 * sc);
      ctx.stroke();
    }

    // ── Front fork
    ctx.strokeStyle = bikePartsCol;
    ctx.lineWidth   = Math.max(1.5, 2.5 * sc);
    ctx.beginPath();
    ctx.moveTo(-0.06 * bScale, -(0.22) * bScale);
    ctx.lineTo(-0.22 * bScale, -(-0.30) * bScale);
    ctx.stroke();

    // ── Rider body + head
    const riderBodyCol = theme.rider_body || '#667799';
    const riderHeadCol = theme.rider_head || '#8899aa';

    ctx.beginPath();
    ctx.arc(0.0 * bScale, -(0.26) * bScale, 0.10 * sc * 0.7 * bScale, 0, Math.PI * 2);
    ctx.fillStyle   = riderBodyCol;
    ctx.globalAlpha = 0.90;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(0.0 * bScale, -(0.50) * bScale, 0.07 * sc * 0.7 * bScale, 0, Math.PI * 2);
    ctx.fillStyle = riderHeadCol;
    ctx.fill();

    ctx.globalAlpha = 1;
    ctx.restore();  // undo rotation

    // ── Value text (rotated canvas is restored now)
    const fsVal   = Math.max(10, Math.min(Math.round(24 * sc), Math.round(w * 0.18)));
    const fsLabel = Math.max(7,  Math.min(Math.round(8  * sc), Math.round(w * 0.07)));

    const direction = value > 0 ? 'R' : (value < 0 ? 'L' : '');
    const valStr    = `${direction}${absLean.toFixed(1)}${unit}`;

    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle    = leanCol;
    ctx.font         = `bold ${fsVal}px 'Segoe UI', sans-serif`;
    ctx.fillText(valStr, cx, cy + R * 0.82);

    ctx.fillStyle = theme.label;
    ctx.font      = `${fsLabel}px 'Segoe UI', sans-serif`;
    ctx.fillText(label, cx, cy - R * 0.85);

    ctx.restore();
  }
};
