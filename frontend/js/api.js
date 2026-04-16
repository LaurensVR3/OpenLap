/**
 * api.js — Bridge between JavaScript and the Python webview_api.WebviewAPI class.
 *
 * All methods return Promises. Python push-events arrive as CustomEvents on window
 * with type 'openlap' and a detail: {type, ...} payload.
 *
 * In dev mode (no pywebview), calls fall through to _mock stubs so the page
 * can still render without crashing.
 */
const API = (() => {
  // ── Raw call ─────────────────────────────────────────────────────────────────
  async function call(method, ...args) {
    if (window.pywebview && window.pywebview.api) {
      return window.pywebview.api[method](...args);
    }
    // Dev-mode mock — returns empty/safe defaults
    console.warn(`[API mock] ${method}`, args);
    return _mock(method);
  }

  function _mock(method) {
    const mocks = {
      get_config:        () => ({ telemetry_path: '', video_path: '', export_path: '',
                                  racebox_path: '', aim_path: '', motec_path: '', gpx_path: '',
                                  offsets: {}, bike_overrides: {}, presets: {},
                                  active_preset: '', session_info: {},
                                  overlay: { is_bike: false, theme: 'Dark', gauges: [] } }),
      scan_sessions:     () => [],
      get_laps:          () => [],
      load_lap_history:  () => [],
      save_config:       () => null,
      get_overlay:       () => ({ is_bike: false, theme: 'Dark', gauges: [] }),
      save_overlay:      () => null,
      save_overlay_as:   () => null,
      list_presets:      () => [],
      open_folder_dialog:() => null,
      open_file_dialog:  () => null,
      start_export:      () => null,
      cancel_export:     () => null,
      get_weather:       () => ({ weather: '—', wind: '—' }),
      edit_session_info: () => null,
      racebox_login:     () => ({ ok: false, error: 'mock' }),
      check_encoders:    () => ({ version: 'mock', encoders: [
        { name: 'libx264', label: 'H.264 software', available: true },
      ]}),
      get_about_info:    () => ({ version: '0.0.0-mock', python: '3.x.x', config: '~/.openlap/config.json' }),
      get_session_meta:  () => ({ track: '', laps: '', best: '', best_secs: null }),
      get_video_server_port:    () => 0,
      save_sessions_cache:      () => null,
      convert_xrk_session:       () => ({ ok: false, error: 'mock' }),
      assign_video:              () => null,
      aim_dll_status:                () => ({ found: false, path: '' }),
      download_aim_dll:              () => null,
      download_racebox_sessions:     () => null,
      cancel_racebox_download:       () => null,
      racebox_playwright_status:     () => ({ playwright: false, chromium: false }),
      install_playwright_chromium:   () => null,
    };
    const fn = mocks[method];
    return fn ? fn() : null;
  }

  // ── Event bus (Python → JS push events) ──────────────────────────────────────
  const _handlers = {};

  window.addEventListener('openlap', e => {
    const { type, ...payload } = e.detail || {};
    (_handlers[type] || []).forEach(cb => cb(payload));
    (_handlers['*'] || []).forEach(cb => cb({ type, ...payload }));
  });

  function on(type, cb) {
    if (!_handlers[type]) _handlers[type] = [];
    _handlers[type].push(cb);
    return () => { _handlers[type] = _handlers[type].filter(f => f !== cb); };
  }

  // ── Public API ────────────────────────────────────────────────────────────────
  return {
    on,

    getConfig:         ()              => call('get_config'),
    saveConfig:        (data)          => call('save_config', data),

    openFolderDialog:  ()              => call('open_folder_dialog'),
    openFileDialog:    (filters)       => call('open_file_dialog', filters),

    scanSessions:      (folder)        => call('scan_sessions', folder),
    getLaps:           (csvPath)       => call('get_laps', csvPath),
    loadLapHistory:    (csvPath, lapIdx) => call('load_lap_history', csvPath, lapIdx),

    getOverlay:        ()              => call('get_overlay'),
    saveOverlay:       (data)          => call('save_overlay', data),
    saveOverlayAs:     (name, data)    => call('save_overlay_as', name, data),
    listPresets:       ()              => call('list_presets'),

    startExport:       (params)        => call('start_export', params),
    cancelExport:      ()              => call('cancel_export'),

    getWeather:        (lat, lon, dt)  => call('get_weather', lat, lon, dt),
    editSessionInfo:   (path, overrides) => call('edit_session_info', path, overrides),

    getSessionMeta:    (csvPath)       => call('get_session_meta', csvPath),
    getVideoServerPort:  ()             => call('get_video_server_port'),
    saveSessionsCache:   (sessions)     => call('save_sessions_cache', sessions),

    raceboxLogin:      (email, password) => call('racebox_login', email, password),
    checkEncoders:     ()              => call('check_encoders'),
    getAboutInfo:      ()              => call('get_about_info'),

    convertXrkSession:        (csvPath)            => call('convert_xrk_session', csvPath),
    assignVideo:              (csvPath, videoPath) => call('assign_video', csvPath, videoPath),
    aimDllStatus:               ()                   => call('aim_dll_status'),
    downloadAimDll:             ()                   => call('download_aim_dll'),
    downloadRaceboxSessions:    ()                   => call('download_racebox_sessions'),
    cancelRaceboxDownload:      ()                   => call('cancel_racebox_download'),
    raceboxPlaywrightStatus:    ()                   => call('racebox_playwright_status'),
    installPlaywrightChromium:  ()                   => call('install_playwright_chromium'),
  };
})();
