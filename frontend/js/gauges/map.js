/**
 * map.js — GPS circuit map gauge.
 *
 * Mirrors styles/map_circuit.py and styles/map_progress.py
 *
 * data keys: lats (array), lons (array), cur_idx (int)
 * theme keys: map_bg_rgba, map_track_outer, map_track_inner, map_driven, map_dot, map_start
 */
const GaugeMap = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    // Background
    ctx.fillStyle = theme.map_bg_rgba || 'rgba(0,0,0,0.65)';
    ctx.beginPath();
    GaugeBase.roundRect(ctx, 2, 2, w - 4, h - 4, Math.max(4, Math.round(Math.min(w, h) * 0.04)));
    ctx.fill();

    const lats   = data.lats   || [];
    const lons   = data.lons   || [];
    const curIdx = data.cur_idx ?? 0;

    if (lats.length < 2 || lons.length < 2) {
      ctx.fillStyle    = theme.label || '#4e6578';
      ctx.font         = `${Math.round(w * 0.08)}px 'Segoe UI', sans-serif`;
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('No GPS', w * 0.5, h * 0.5);
      return;
    }

    // Compute bounding box
    const pad = 0.10;
    const minLat = Math.min(...lats);
    const maxLat = Math.max(...lats);
    const minLon = Math.min(...lons);
    const maxLon = Math.max(...lons);
    const spanLat = maxLat - minLat || 1e-6;
    const spanLon = maxLon - minLon || 1e-6;

    // Scale preserving aspect ratio
    const availW = w * (1 - 2 * pad);
    const availH = h * (1 - 2 * pad);
    const scaleX = availW / spanLon;
    const scaleY = availH / spanLat;
    const scale  = Math.min(scaleX, scaleY);
    const offX   = w * pad + (availW - spanLon * scale) / 2;
    const offY   = h * pad + (availH - spanLat * scale) / 2;

    function toScreen(lat, lon) {
      return {
        x: offX + (lon - minLon) * scale,
        y: h - offY - (lat - minLat) * scale,  // flip y (north up)
      };
    }

    const n = Math.min(lats.length, lons.length);

    // Full track outline (outer)
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const p = toScreen(lats[i], lons[i]);
      i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
    }
    ctx.closePath();
    ctx.strokeStyle = theme.map_track_outer || '#1a2a3a';
    ctx.lineWidth   = Math.max(4, w * 0.03);
    ctx.lineCap     = 'round';
    ctx.lineJoin    = 'round';
    ctx.stroke();

    // Full track inner
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const p = toScreen(lats[i], lons[i]);
      i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
    }
    ctx.closePath();
    ctx.strokeStyle = theme.map_track_inner || '#2255aa';
    ctx.lineWidth   = Math.max(2, w * 0.015);
    ctx.stroke();

    // Driven portion (from start to cur_idx)
    if (curIdx > 0) {
      ctx.beginPath();
      for (let i = 0; i <= Math.min(curIdx, n - 1); i++) {
        const p = toScreen(lats[i], lons[i]);
        i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y);
      }
      ctx.strokeStyle = theme.map_driven || '#ffffff';
      ctx.lineWidth   = Math.max(1.5, w * 0.010);
      ctx.stroke();
    }

    // Start marker
    const pStart = toScreen(lats[0], lons[0]);
    ctx.beginPath();
    ctx.arc(pStart.x, pStart.y, Math.max(3, w * 0.02), 0, Math.PI * 2);
    ctx.fillStyle = theme.map_start || '#00ff88';
    ctx.fill();

    // Current position dot
    const idx   = Math.max(0, Math.min(curIdx, n - 1));
    const pDot  = toScreen(lats[idx], lons[idx]);
    const dotR  = Math.max(4, w * 0.025);

    ctx.beginPath();
    ctx.arc(pDot.x, pDot.y, dotR, 0, Math.PI * 2);
    ctx.fillStyle = theme.map_dot || '#ff2222';
    ctx.fill();
    ctx.strokeStyle = 'white';
    ctx.lineWidth   = Math.max(1, w * 0.006);
    ctx.stroke();
  }
};
