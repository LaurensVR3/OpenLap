import { describe, it, expect } from 'vitest';
import { loadGaugeBase, makeFakeCanvasCtx } from './helpers.js';

describe('GaugeBase.fitFontSize', () => {
  const GaugeBase = loadGaugeBase();

  it('returns the candidate size unchanged when the text already fits', () => {
    const ctx = makeFakeCanvasCtx();
    const size = GaugeBase.fitFontSize(ctx, '5', 20, 'bold', 500);
    expect(size).toBe(20);
  });

  it('shrinks the size when the text is wider than the budget', () => {
    const ctx = makeFakeCanvasCtx();
    const size = GaugeBase.fitFontSize(ctx, '123,456,789', 34, 'bold', 60);
    expect(size).toBeLessThan(34);
  });

  it('shrinks proportionally more for longer strings at the same budget', () => {
    const ctx = makeFakeCanvasCtx();
    const shortFit = GaugeBase.fitFontSize(ctx, '12', 34, 'bold', 60);
    const longFit  = GaugeBase.fitFontSize(ctx, '123456789', 34, 'bold', 60);
    expect(longFit).toBeLessThanOrEqual(shortFit);
  });

  it('never returns less than minFontSizePx', () => {
    const ctx = makeFakeCanvasCtx();
    const size = GaugeBase.fitFontSize(ctx, 'a very long string indeed', 40, 'bold', 2, 6);
    expect(size).toBe(6);
  });

  it('never grows past the candidate size when the candidate is already below minFontSizePx', () => {
    const ctx = makeFakeCanvasCtx();
    const size = GaugeBase.fitFontSize(ctx, '123456789', 5, 'bold', 2, 6);
    expect(size).toBeLessThanOrEqual(5);
  });

  it('sets ctx.font using the requested font family', () => {
    const ctx = makeFakeCanvasCtx();
    GaugeBase.fitFontSize(ctx, '1:23.456', 20, 'bold', 500, 8, "'Consolas', monospace");
    expect(ctx.font).toContain('Consolas');
  });
});
