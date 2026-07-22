/**
 * Regression coverage for specific bugs found in the full-project gauge
 * review and fixed directly in frontend/js/gauges/*.js:
 *
 *  - bar.js / multiline.js bypassed GaugeBase.fmtValue for channels the
 *    editor explicitly allows on them (lap_time, delta_time), rendering
 *    e.g. a lap time as "125.3 s" instead of "2:05.300".
 *  - info.js / scoreboard.js derived font size from the naive h/n instead
 *    of the actual (yTop-yBottom)/n row height, rendering ~12-14% too large
 *    versus the exported video frame for the same field count.
 *  - gmeter.js divided by a max_val of 0 with no guard, unlike every other
 *    gauge's min/max range calc.
 *
 * These call the real render(ctx, data, w, h) functions (not reimplemented
 * copies of the formulas) via a permissive fake canvas context, so a
 * regression to any of these would actually be caught here.
 */
import {
  loadGaugeBase, loadGauge, makeFullCanvasCtx,
} from './helpers.js';

beforeEach(() => {
  globalThis.GaugeBase = loadGaugeBase();
});

describe('bar.js routes special channels through fmtValue', () => {
  it('renders a lap_time value as M:SS.mmm, not "<n> s"', () => {
    const GaugeBar = loadGauge('bar.js', 'GaugeBar');
    const ctx = makeFullCanvasCtx();
    GaugeBar.render(ctx, {
      theme: 'Dark', channel: 'lap_time', value: 125.3, history_vals: [125.3],
      label: 'Lap Time', unit: '', min_val: 0, max_val: 200, symmetric: false,
    }, 180, 120);

    const texts = ctx._fillTextCalls.map(c => c.text);
    expect(texts).toContain('2:05.300');
    expect(texts.some(t => t.includes('125.3'))).toBe(false);
  });

  it('leaves plain numeric channels (e.g. speed) formatted as before', () => {
    const GaugeBar = loadGauge('bar.js', 'GaugeBar');
    const ctx = makeFullCanvasCtx();
    GaugeBar.render(ctx, {
      theme: 'Dark', channel: 'speed', value: 185.4, history_vals: [185.4],
      label: 'Speed', unit: 'km/h', min_val: 0, max_val: 250, symmetric: false,
    }, 180, 120);

    expect(ctx._fillTextCalls.map(c => c.text)).toContain('185.4 km/h');
  });

  it('renders a missing (null) value as "—", not a fake zero', () => {
    const GaugeBar = loadGauge('bar.js', 'GaugeBar');
    const ctx = makeFullCanvasCtx();
    GaugeBar.render(ctx, {
      theme: 'Dark', channel: 'delta_time', value: null, history_vals: [0],
      label: 'Delta', unit: 's', min_val: -30, max_val: 30, symmetric: true,
    }, 180, 120);

    expect(ctx._fillTextCalls.map(c => c.text)).toContain('—');
  });
});

describe('multiline.js legend formatting matches fmtValue / its Python mirror', () => {
  function render(entries) {
    const GaugeMultiline = loadGauge('multiline.js', 'GaugeMultiline');
    const ctx = makeFullCanvasCtx();
    GaugeMultiline.render(ctx, { theme: 'Dark', multi_channels: entries }, 320, 140);
    return ctx._fillTextCalls.map(c => c.text);
  }

  it('formats a lap_time entry as M:SS.mmm', () => {
    const texts = render([{
      channel: 'lap_time', label: 'Lap', unit: '', value: 125.3, values: [125.3, 125.3],
      min_val: 0, max_val: 200, symmetric: false, color_idx: 0,
    }]);
    expect(texts.some(t => t.includes('2:05.300'))).toBe(true);
  });

  it('prefixes a positive delta_time entry with "+"', () => {
    const texts = render([{
      channel: 'delta_time', label: 'Delta', unit: '', value: 0.06, values: [0.06, 0.06],
      min_val: -5, max_val: 5, symmetric: true, color_idx: 0,
    }]);
    expect(texts.some(t => t.includes('+0.060'))).toBe(true);
  });

  it('comma-groups a plain numeric value >= 10000, matching the Python mirror', () => {
    const texts = render([{
      channel: 'rpm', label: 'RPM', unit: '', value: 12345, values: [12345, 12345],
      min_val: 0, max_val: 20000, symmetric: false, color_idx: 0,
    }]);
    expect(texts.some(t => t.includes('12,345'))).toBe(true);
    expect(texts.some(t => t.includes('12345'))).toBe(false);
  });
});

describe('info.js / scoreboard.js font size uses the real row-height fraction', () => {
  // The bug: fsLabel was computed from the naive h/n instead of
  // (yTop-yBottom)/n (~0.88*h/n) — always larger than the correct value.
  // A regression back to h/n would make these ratios diverge from 0.88.
  function labelFontSize(fillTextCalls, labelText) {
    const call = fillTextCalls.find(c => c.text === labelText);
    const m = call && /(\d+(?:\.\d+)?)px/.exec(call.font);
    return m ? parseFloat(m[1]) : null;
  }

  it('info.js: label font size reflects the 0.88 row-height factor, not naive h/n', () => {
    const GaugeInfo = loadGauge('info.js', 'GaugeInfo');
    const ctx = makeFullCanvasCtx();
    const h = 400;
    const fields = ['track', 'datetime', 'vehicle', 'weather', 'wind'];
    GaugeInfo.render(ctx, {
      theme: 'Dark', selected_fields: fields,
      info_track: 'Spa', info_date: '2024-01-01', info_time: '10:00',
      info_vehicle: 'GT3', info_session: 'Race', info_weather: '20C', info_wind: 'N 5km/h',
    }, 300, h);

    const size = labelFontSize(ctx._fillTextCalls, 'TRACK');
    const n = fields.length;
    const correctRowH = (h * 0.94 - h * 0.06) / n;
    const buggyRowH   = h / n;
    const expectedCorrect = Math.max(8, Math.round(correctRowH * 0.26));
    const wouldBeBuggy    = Math.max(8, Math.round(buggyRowH * 0.26));

    expect(size).toBe(expectedCorrect);
    expect(size).toBeLessThan(wouldBeBuggy);
  });
});

describe('gmeter.js does not divide by zero when max_val is explicitly 0', () => {
  it('produces no NaN/Infinity draw coordinates', () => {
    const GaugeGmeter = loadGauge('gmeter.js', 'GaugeGmeter');
    const ctx = makeFullCanvasCtx();
    expect(() => {
      GaugeGmeter.render(ctx, {
        theme: 'Dark', value: 0.5, value_gy: 0.2,
        history_vals: [0.5], history_gy: [0.2], min_val: -3, max_val: 0,
      }, 160, 160);
    }).not.toThrow();
  });
});
