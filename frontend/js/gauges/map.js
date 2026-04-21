/**
 * map.js — GPS circuit map gauge.
 *
 * Mirrors styles/map_circuit.py and styles/map_progress.py
 *
 * data keys: lats (array), lons (array), cur_idx (int)
 * theme keys: map_bg_rgba, map_track_outer, map_track_inner, map_driven, map_dot, map_start
 */

/**
 * Smooth polyline using midpoint quadratic bezier (Chaikin-style).
 * xs/ys are pre-projected screen coordinate arrays.
 * closed=true joins the last point back to the first seamlessly.
 */
function _strokeSmooth(ctx, xs, ys, closed) {
  const n = Math.min(xs.length, ys.length);
  if (n < 2) return;
  ctx.beginPath();
  if (closed && n > 2) {
    ctx.moveTo((xs[n - 1] + xs[0]) / 2, (ys[n - 1] + ys[0]) / 2);
    for (let i = 0; i < n - 1; i++) {
      ctx.quadraticCurveTo(xs[i], ys[i], (xs[i] + xs[i + 1]) / 2, (ys[i] + ys[i + 1]) / 2);
    }
    ctx.quadraticCurveTo(xs[n - 1], ys[n - 1], (xs[n - 1] + xs[0]) / 2, (ys[n - 1] + ys[0]) / 2);
    ctx.closePath();
  } else {
    ctx.moveTo(xs[0], ys[0]);
    for (let i = 1; i < n - 1; i++) {
      ctx.quadraticCurveTo(xs[i], ys[i], (xs[i] + xs[i + 1]) / 2, (ys[i] + ys[i + 1]) / 2);
    }
    ctx.lineTo(xs[n - 1], ys[n - 1]);
  }
}

const GaugeMap = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    // Background
    ctx.fillStyle = theme.map_bg_rgba || 'rgba(0,0,0,0.65)';
    ctx.beginPath();
    GaugeBase.roundRect(ctx, 2, 2, w - 4, h - 4, Math.max(4, Math.round(Math.min(w, h) * 0.04)));
    ctx.fill();

    const lats     = data.lats   || [];
    const lons     = data.lons   || [];
    const curIdx   = data.cur_idx ?? 0;
    const osmLats  = data.track_map_lats  || [];
    const osmLons  = data.track_map_lons  || [];
    const osmAreas = data.track_map_areas || [];

    if (lats.length < 2 || lons.length < 2) {
      ctx.fillStyle    = theme.label || '#4e6578';
      ctx.font         = `${Math.round(w * 0.08)}px 'Segoe UI', sans-serif`;
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('No GPS', w * 0.5, h * 0.5);
      return;
    }

    // Compute bounding box from GPS trace + OSM geometry combined
    const pad     = 0.10;
    const allLats = osmLats.length ? [...lats, ...osmLats] : lats;
    const allLons = osmLons.length ? [...lons, ...osmLons] : lons;
    const minLat  = Math.min(...allLats);
    const maxLat  = Math.max(...allLats);
    const minLon  = Math.min(...allLons);
    const maxLon  = Math.max(...allLons);
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

    // Pre-project screen coordinates once per dataset
    const osmXs = [], osmYs = [];
    if (osmLats.length >= 2 && osmLons.length >= 2) {
      const on = Math.min(osmLats.length, osmLons.length);
      for (let i = 0; i < on; i++) {
        const p = toScreen(osmLats[i], osmLons[i]);
        osmXs.push(p.x); osmYs.push(p.y);
      }
    }

    const n = Math.min(lats.length, lons.length);
    const gpsXs = [], gpsYs = [];
    for (let i = 0; i < n; i++) {
      const p = toScreen(lats[i], lons[i]);
      gpsXs.push(p.x); gpsYs.push(p.y);
    }

    // OSM area polygons — filled track surface, drawn first (lowest layer)
    if (osmAreas.length) {
      ctx.fillStyle = 'rgba(74,85,104,0.55)';
      for (const area of osmAreas) {
        const aLats = area.lats || [];
        const aLons = area.lons || [];
        const an = Math.min(aLats.length, aLons.length);
        if (an < 3) continue;
        ctx.beginPath();
        for (let i = 0; i < an; i++) {
          const p = toScreen(aLats[i], aLons[i]);
          if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
        }
        ctx.closePath();
        ctx.fill();
      }
    }

    // OSM road background — smoothed, drawn below GPS trace
    if (osmXs.length >= 2) {
      ctx.lineCap  = 'round';
      ctx.lineJoin = 'round';
      _strokeSmooth(ctx, osmXs, osmYs, false);
      ctx.strokeStyle = '#4a5568';
      ctx.lineWidth   = Math.max(6, w * 0.045);
      ctx.stroke();

      _strokeSmooth(ctx, osmXs, osmYs, false);
      ctx.strokeStyle = '#2d3748';
      ctx.lineWidth   = Math.max(4, w * 0.028);
      ctx.stroke();
    }

    // Full track outline (outer) — smoothed closed circuit
    ctx.lineCap  = 'round';
    ctx.lineJoin = 'round';
    _strokeSmooth(ctx, gpsXs, gpsYs, true);
    ctx.strokeStyle = theme.map_track_outer || '#1a2a3a';
    ctx.lineWidth   = Math.max(4, w * 0.03);
    ctx.stroke();

    // Full track inner
    _strokeSmooth(ctx, gpsXs, gpsYs, true);
    ctx.strokeStyle = theme.map_track_inner || '#2255aa';
    ctx.lineWidth   = Math.max(2, w * 0.015);
    ctx.stroke();

    // Driven portion (from start to cur_idx) — smoothed open segment
    if (curIdx > 0) {
      const di = Math.min(curIdx, n - 1) + 1;
      _strokeSmooth(ctx, gpsXs.slice(0, di), gpsYs.slice(0, di), false);
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
  },

  /**
   * Zoomed map: centred on the current GPS position, configurable radius,
   * optional reference-lap trace rendered in purple.
   */
  renderZoomed(ctx, data, w, h) {
    const theme      = GaugeBase.getTheme(data.theme || 'Dark');
    const lats       = data.lats   || [];
    const lons       = data.lons   || [];
    const curIdx     = data.cur_idx ?? 0;
    const radius     = Math.max(10, data.zoom_radius_m ?? 150);
    const showRef    = data.show_ref !== false;
    const refLats    = data.ref_lats || [];
    const refLons    = data.ref_lons || [];
    const refCurIdx  = data.ref_cur_idx ?? 0;
    const osmLats    = data.track_map_lats  || [];
    const osmLons    = data.track_map_lons  || [];
    const osmAreas   = data.track_map_areas || [];

    if (lats.length < 2) {
      ctx.fillStyle    = theme.label || '#4e6578';
      ctx.font         = `${Math.round(w * 0.08)}px 'Segoe UI', sans-serif`;
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('No GPS', w * 0.5, h * 0.5);
      return;
    }

    const safeIdx   = Math.max(0, Math.min(curIdx, lats.length - 1));
    const centerLat = lats[safeIdx];
    const centerLon = lons[safeIdx];

    // GPS → local metres
    const LAT_M = 111000;
    const LON_M = 111000 * Math.cos(centerLat * Math.PI / 180);

    // canvas: centre maps to current position; radius maps to half the smaller dimension
    const pad   = 0.08;
    const avail = Math.min(w, h) * (1 - 2 * pad);
    const scale = avail * 0.5 / radius;
    const cx    = w / 2;
    const cy    = h / 2;

    function toScreen(lat, lon) {
      return {
        x: cx + (lon - centerLon) * LON_M * scale,
        y: cy - (lat - centerLat) * LAT_M * scale,
      };
    }

    // Clip to gauge bounds
    ctx.save();
    ctx.beginPath();
    GaugeBase.roundRect(ctx, 4, 4, w - 8, h - 8, Math.max(3, Math.round(Math.min(w, h) * 0.03)));
    ctx.clip();

    const n = Math.min(lats.length, lons.length);

    // Pre-project screen coordinates once per dataset
    const osmXsZ = [], osmYsZ = [];
    if (osmLats.length >= 2 && osmLons.length >= 2) {
      const on = Math.min(osmLats.length, osmLons.length);
      for (let i = 0; i < on; i++) {
        const p = toScreen(osmLats[i], osmLons[i]);
        osmXsZ.push(p.x); osmYsZ.push(p.y);
      }
    }

    const gpsXsZ = [], gpsYsZ = [];
    for (let i = 0; i < n; i++) {
      const p = toScreen(lats[i], lons[i]);
      gpsXsZ.push(p.x); gpsYsZ.push(p.y);
    }

    // OSM area polygons — filled track surface, drawn first (lowest layer)
    if (osmAreas.length) {
      ctx.fillStyle = 'rgba(74,85,104,0.55)';
      for (const area of osmAreas) {
        const aLats = area.lats || [];
        const aLons = area.lons || [];
        const an = Math.min(aLats.length, aLons.length);
        if (an < 3) continue;
        ctx.beginPath();
        for (let i = 0; i < an; i++) {
          const p = toScreen(aLats[i], aLons[i]);
          if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
        }
        ctx.closePath();
        ctx.fill();
      }
    }

    // OSM road background — smoothed
    if (osmXsZ.length >= 2) {
      ctx.lineCap  = 'round';
      ctx.lineJoin = 'round';
      _strokeSmooth(ctx, osmXsZ, osmYsZ, false);
      ctx.strokeStyle = '#4a5568';
      ctx.lineWidth   = Math.max(6, w * 0.045);
      ctx.stroke();

      _strokeSmooth(ctx, osmXsZ, osmYsZ, false);
      ctx.strokeStyle = '#2d3748';
      ctx.lineWidth   = Math.max(4, w * 0.028);
      ctx.stroke();
    }

    // Full track — outer, smoothed closed circuit
    ctx.lineCap  = 'round';
    ctx.lineJoin = 'round';
    _strokeSmooth(ctx, gpsXsZ, gpsYsZ, true);
    ctx.strokeStyle = theme.map_track_outer || '#1a2a3a';
    ctx.lineWidth   = Math.max(4, w * 0.03);
    ctx.stroke();

    // Full track — inner
    _strokeSmooth(ctx, gpsXsZ, gpsYsZ, true);
    ctx.strokeStyle = theme.map_track_inner || '#2255aa';
    ctx.lineWidth   = Math.max(2, w * 0.015);
    ctx.stroke();

    // Reference lap trace (purple) — smoothed
    if (showRef && refLats.length >= 2 && refLons.length >= 2) {
      const refXs = [], refYs = [];
      const rn = Math.min(refLats.length, refLons.length);
      for (let i = 0; i < rn; i++) {
        const p = toScreen(refLats[i], refLons[i]);
        refXs.push(p.x); refYs.push(p.y);
      }
      _strokeSmooth(ctx, refXs, refYs, true);
      ctx.strokeStyle = '#cc44ff';
      ctx.lineWidth   = Math.max(2, w * 0.013);
      ctx.stroke();
    }

    // Driven portion — smoothed open segment
    if (curIdx > 0) {
      const di = Math.min(curIdx, n - 1) + 1;
      _strokeSmooth(ctx, gpsXsZ.slice(0, di), gpsYsZ.slice(0, di), false);
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

    // Reference position dot (purple, slightly smaller than main dot)
    if (showRef && refLats.length >= 2) {
      const safeRefIdx = Math.max(0, Math.min(refCurIdx, refLats.length - 1));
      const pRef = toScreen(refLats[safeRefIdx], refLons[safeRefIdx]);
      const refDotR = Math.max(3, w * 0.022);
      ctx.beginPath();
      ctx.arc(pRef.x, pRef.y, refDotR, 0, Math.PI * 2);
      ctx.fillStyle = '#cc44ff';
      ctx.fill();
      ctx.strokeStyle = 'white';
      ctx.lineWidth   = Math.max(1, w * 0.006);
      ctx.stroke();
    }

    // Current position dot
    const pDot = toScreen(lats[safeIdx], lons[safeIdx]);
    const dotR = Math.max(4, w * 0.028);
    ctx.beginPath();
    ctx.arc(pDot.x, pDot.y, dotR, 0, Math.PI * 2);
    ctx.fillStyle = theme.map_dot || '#ff2222';
    ctx.fill();
    ctx.strokeStyle = 'white';
    ctx.lineWidth   = Math.max(1, w * 0.007);
    ctx.stroke();

    ctx.restore();

    // Feather/fade edges using destination-out with a radial gradient
    const featherR = Math.min(w, h) * 0.5;
    const grad = ctx.createRadialGradient(cx, cy, featherR * 0.65, cx, cy, featherR);
    grad.addColorStop(0, 'rgba(0,0,0,0)');
    grad.addColorStop(1, 'rgba(0,0,0,1)');
    ctx.save();
    ctx.globalCompositeOperation = 'destination-out';
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
    ctx.restore();
  },
};
