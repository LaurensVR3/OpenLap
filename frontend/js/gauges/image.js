/**
 * image.js — Image overlay gauge (logo / watermark).
 *
 * data keys:
 *   image_url   — absolute HTTP URL (from file server) or empty string
 *   image_path  — Windows path (for display only)
 *   opacity     — 0.0–1.0 (default 1.0)
 *   fit         — 'contain' | 'cover' | 'stretch' (default 'contain')
 */

const GaugeImage = (() => {
  // Cache: url → HTMLImageElement (null = failed)
  const _cache = {};

  function _placeholder(ctx, w, h) {
    const cell = Math.max(4, Math.round(Math.min(w, h) / 8));
    for (let y = 0; y < h; y += cell) {
      for (let x = 0; x < w; x += cell) {
        ctx.fillStyle = ((Math.floor(x / cell) + Math.floor(y / cell)) % 2 === 0)
          ? '#2a2a2a' : '#181818';
        ctx.fillRect(x, y, Math.min(cell, w - x), Math.min(cell, h - y));
      }
    }
    ctx.fillStyle = 'rgba(255,255,255,0.35)';
    const fs = Math.max(8, Math.round(Math.min(w, h) * 0.14));
    ctx.font = `${fs}px sans-serif`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('IMAGE', w / 2, h / 2);
  }

  function _drawImage(ctx, img, w, h, fit, opacity) {
    const iw = img.naturalWidth;
    const ih = img.naturalHeight;
    let dx = 0, dy = 0, dw = w, dh = h;

    if (fit === 'contain') {
      const scale = Math.min(w / iw, h / ih);
      dw = iw * scale;
      dh = ih * scale;
      dx = (w - dw) / 2;
      dy = (h - dh) / 2;
    } else if (fit === 'cover') {
      const scale = Math.max(w / iw, h / ih);
      dw = iw * scale;
      dh = ih * scale;
      dx = (w - dw) / 2;
      dy = (h - dh) / 2;
    }
    // 'stretch' → use full dx/dy/dw/dh as-is

    ctx.save();
    ctx.globalAlpha = Math.max(0, Math.min(1, opacity ?? 1.0));
    ctx.drawImage(img, dx, dy, dw, dh);
    ctx.restore();
  }

  return {
    render(ctx, data, w, h) {
      const url     = data.image_url  || '';
      const opacity = data.opacity    ?? 1.0;
      const fit     = data.fit        || 'contain';

      if (!url) {
        _placeholder(ctx, w, h);
        return;
      }

      const cached = _cache[url];

      if (cached === null) {
        // Previously failed — show placeholder
        _placeholder(ctx, w, h);
        return;
      }

      if (cached && cached.complete && cached.naturalWidth > 0) {
        _drawImage(ctx, cached, w, h, fit, opacity);
        return;
      }

      if (!cached) {
        // Start loading
        const img = new Image();
        _cache[url] = img;           // mark as pending (truthy)
        img.onload  = () => { /* next render will pick it up */ };
        img.onerror = () => { _cache[url] = null; };
        img.src = url;
      }

      // Still loading — show placeholder this frame
      _placeholder(ctx, w, h);
    },

    clearCache(url) {
      if (url) delete _cache[url];
      else Object.keys(_cache).forEach(k => delete _cache[k]);
    },
  };
})();
