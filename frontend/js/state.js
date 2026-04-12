/**
 * state.js — Simple global reactive state store.
 * Components subscribe via State.on('key', cb) and update via State.set('key', value).
 */
const State = (() => {
  const _data = {
    config:        null,      // AppConfig dict from Python
    sessions:      [],        // list of session dicts from last scan
    selectedItems: [],        // items queued for export [{csv_path, lap_idx, video_paths, ...}]
    scanStatus:    'idle',    // 'idle' | 'scanning' | 'done' | 'error'
    scanMessage:   '',
  };

  const _listeners = {};

  function on(key, cb) {
    if (!_listeners[key]) _listeners[key] = [];
    _listeners[key].push(cb);
    return () => {  // returns unsubscribe function
      _listeners[key] = _listeners[key].filter(f => f !== cb);
    };
  }

  function set(key, value) {
    _data[key] = value;
    (_listeners[key] || []).forEach(cb => cb(value));
  }

  function get(key) {
    return _data[key];
  }

  return { on, set, get };
})();
