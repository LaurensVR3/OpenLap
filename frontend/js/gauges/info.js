/**
 * info.js — Session info panel gauge.
 *
 * Mirrors styles/gauge_info.py
 *
 * data keys:
 *   selected_fields  — list of field keys to show (e.g. ['track','datetime','weather','wind'])
 *   info_track, info_date, info_time, info_vehicle, info_session,
 *   info_weather, info_wind
 * theme keys: bg, bgEdge, text, label, fillPos
 */

const INFO_FIELDS_DEFAULT = ['track', 'datetime', 'vehicle', 'weather', 'wind'];

const GaugeInfo = {
  render(ctx, data, w, h) {
    const theme = GaugeBase.getTheme(data.theme || 'Dark');

    GaugeBase.drawBackground(ctx, w, h, theme);
    GaugeBase.drawAccentBar(ctx, w, h, theme.fillPos);

    // Build value pairs for selected fields
    const allValues = {};

    const track  = data.info_track || '';
    allValues.track = ['TRACK', track || '—'];

    const dateS  = data.info_date || '';
    const timeS  = data.info_time || '';
    if (dateS && timeS)       allValues.datetime = ['DATE', `${dateS}  ${timeS}`];
    else if (dateS)           allValues.datetime = ['DATE', dateS];
    else                      allValues.datetime = ['DATE', '—'];

    const vehicle = data.info_vehicle || '';
    allValues.vehicle = ['VEHICLE', vehicle || '—'];

    const sessionT = data.info_session || '';
    allValues.session = ['SESSION', sessionT || '—'];

    const weather = data.info_weather || '';
    allValues.weather = ['WEATHER', weather || '—'];

    const wind = data.info_wind || '';
    allValues.wind = ['WIND', wind || '—'];

    const selected = (data.selected_fields && data.selected_fields.length > 0)
      ? data.selected_fields
      : INFO_FIELDS_DEFAULT;

    const fields = selected
      .filter(k => allValues[k])
      .map(k => allValues[k]);

    if (fields.length === 0) fields.push(['INFO', '—']);

    const n       = fields.length;
    const padL    = w * 0.08;
    const yTop    = h * 0.94;
    const yBottom = h * 0.06;
    const rowH    = (yTop - yBottom) / Math.max(n, 1);

    // Font sizes derived from row height, matching gauge_info.py formula:
    // fs = max(4, int(h * row_h * ratio / 1.39))
    // row_h is the fraction 1/n, so row height in px = h/n
    const rowPx   = h / n;
    const fsLabel = Math.max(8,  Math.round(rowPx * 0.26));
    const fsValue = Math.max(10, Math.round(rowPx * 0.48));

    ctx.textBaseline = 'middle';
    ctx.textAlign    = 'left';

    for (let i = 0; i < fields.length; i++) {
      const [lbl, val] = fields[i];
      const yc    = yTop - rowH * (i + 0.5);
      const yLbl  = yc + rowH * 0.20;
      const yVal  = yc - rowH * 0.14;

      ctx.fillStyle = theme.label;
      ctx.font      = `${fsLabel}px 'Segoe UI', sans-serif`;
      ctx.fillText(lbl, padL, yLbl);

      ctx.fillStyle  = theme.text;
      ctx.font       = `bold ${fsValue}px 'Segoe UI', sans-serif`;
      ctx.fillText(val, padL, yVal);
    }
  }
};
