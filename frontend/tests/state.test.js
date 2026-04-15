import { loadState } from './helpers.js';

describe('State — reactive store', () => {

  beforeEach(() => {
    loadState(); // fresh isolated instance for every test
  });

  // ── Initial values ─────────────────────────────────────────────────────────

  test('config starts as null', () => {
    expect(State.get('config')).toBeNull();
  });

  test('sessions starts as empty array', () => {
    expect(State.get('sessions')).toEqual([]);
  });

  test('selectedItems starts as empty array', () => {
    expect(State.get('selectedItems')).toEqual([]);
  });

  test('previewSession starts as null', () => {
    expect(State.get('previewSession')).toBeNull();
  });

  // ── set / get ──────────────────────────────────────────────────────────────

  test('set stores a value retrievable by get', () => {
    State.set('sessions', [{ csv_path: '/foo.csv' }]);
    expect(State.get('sessions')).toEqual([{ csv_path: '/foo.csv' }]);
  });

  test('set overwrites a previous value', () => {
    State.set('scanStatus', 'scanning');
    State.set('scanStatus', 'done');
    expect(State.get('scanStatus')).toBe('done');
  });

  test('setting one key does not affect others', () => {
    State.set('sessions', [{ csv_path: '/a.csv' }]);
    expect(State.get('config')).toBeNull();
    expect(State.get('selectedItems')).toEqual([]);
  });

  // ── Subscriptions ──────────────────────────────────────────────────────────

  test('on fires the callback synchronously when the value changes', () => {
    const cb = vi.fn();
    State.on('scanStatus', cb);
    State.set('scanStatus', 'done');
    expect(cb).toHaveBeenCalledTimes(1);
    expect(cb).toHaveBeenCalledWith('done');
  });

  test('on does not fire for changes to a different key', () => {
    const cb = vi.fn();
    State.on('sessions', cb);
    State.set('scanStatus', 'done');
    expect(cb).not.toHaveBeenCalled();
  });

  test('multiple subscribers all receive updates', () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    State.on('scanStatus', cb1);
    State.on('scanStatus', cb2);
    State.set('scanStatus', 'done');
    expect(cb1).toHaveBeenCalledWith('done');
    expect(cb2).toHaveBeenCalledWith('done');
  });

  test('on returns an unsubscribe function that stops future callbacks', () => {
    const cb = vi.fn();
    const unsub = State.on('config', cb);
    State.set('config', { a: 1 });
    expect(cb).toHaveBeenCalledTimes(1);

    unsub();
    State.set('config', { b: 2 });
    expect(cb).toHaveBeenCalledTimes(1); // no second call
  });

  test('unsubscribing one listener does not affect others on the same key', () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    const unsub1 = State.on('config', cb1);
    State.on('config', cb2);

    unsub1();
    State.set('config', { x: 1 });

    expect(cb1).not.toHaveBeenCalled();
    expect(cb2).toHaveBeenCalledWith({ x: 1 });
  });
});
