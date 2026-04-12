/**
 * numeric.js — Numeric gauge: large centred value with label and unit.
 *
 * Mirrors styles/gauge_numeric.py
 *
 * data keys: value, label, unit, channel
 * theme keys: bg, bgEdge, text, label (colour), unit (colour)
 */
const GaugeNumeric = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');
    const value   = data.value   ?? 0;
    const label   = (data.label  || '').toUpperCase();
    const unit    = data.unit    || '';
    const channel = data.channel || '';

    GaugeBase.drawBackground(ctx, w, h, theme);

    const sc = Math.sqrt((w / 120) * (h / 160));

    // Font sizes mirror gauge_numeric.py's scale_factor logic
    let fsLabel = Math.max(5,  Math.min(Math.round(11 * sc), Math.round(w * 0.13)));
    let fsValue = Math.max(10, Math.min(Math.round(34 * sc), Math.round(w * 0.38)));
    let fsUnit  = Math.max(5,  Math.min(Math.round(9  * sc), Math.round(w * 0.10)));

    // Format value string
    let txt;
    if (channel === 'lap_time') {
      txt     = GaugeBase.fmtValue(value, 'lap_time');
      fsValue = Math.max(8, Math.min(Math.round(20 * sc), Math.round(w * 0.22)));
    } else {
      txt = GaugeBase.fmtValue(value, channel);
    }

    ctx.textBaseline = 'middle';
    ctx.textAlign    = 'center';

    // Label (top 78% position)
    ctx.fillStyle = theme.label;
    ctx.font      = `${fsLabel}px 'Segoe UI', sans-serif`;
    ctx.fillText(label, w * 0.5, h * 0.22);

    // Value (centre)
    ctx.fillStyle  = theme.text;
    ctx.font       = `bold ${fsValue}px 'Segoe UI', sans-serif`;
    ctx.fillText(txt, w * 0.5, h * 0.50);

    // Unit (bottom 24% position)
    ctx.fillStyle = theme.unit;
    ctx.font      = `${fsUnit}px 'Segoe UI', sans-serif`;
    ctx.fillText(unit, w * 0.5, h * 0.78);
  }
};
