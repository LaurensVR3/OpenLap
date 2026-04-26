/**
 * settings.js — Settings page (Phase 4).
 *
 * Sections:
 *   1. Telemetry & video folders (per-source)
 *   2. RaceBox cloud login
 *   3. Encoder / ffmpeg detection
 *   4. About
 */
(function () {
  let _config     = null;
  let _unlistenFns = [];

  // ── Mount / Unmount ──────────────────────────────────────────────────────────

  async function mount(container) {
    _config = await API.getConfig();
    container.innerHTML = _buildHTML(_config);
    _bindEvents(container);
  }

  function unmount() {
    _unlistenFns.forEach(fn => fn());
    _unlistenFns = [];
  }

  // ── HTML ─────────────────────────────────────────────────────────────────────

  function _buildHTML(cfg) {
    return `
<div class="page settings-page">
  <div class="toolbar">
    <div class="toolbar-left">
      <span class="page-title">Settings</span>
    </div>
    <div class="toolbar-right">
      <span id="save-msg" class="save-msg"></span>
      <button class="btn btn-accent" id="save-btn">Save</button>
    </div>
  </div>
  <div class="page-divider"></div>

  <div class="settings-body">

    <!-- Telemetry folders -->
    <section class="settings-section">
      <div class="section-title">Telemetry Folders</div>
      ${_folderRow('RaceBox',     'racebox_path', cfg.racebox_path, 'RaceBox Mini CSV exports')}
      ${_folderRow('AIM Mychron','aim_path',      cfg.aim_path,     'AIM XRK / CSV files')}
      ${_folderRow('MoTeC',       'motec_path',   cfg.motec_path,   'MoTeC .ld binary files')}
      ${_folderRow('GPX',         'gpx_path',     cfg.gpx_path,     '.gpx GPS track files')}
      ${_folderRow('VBOX',        'vbox_path',    cfg.vbox_path,    'Racelogic VBOX .vbo files')}
    </section>

    <!-- Video & output -->
    <section class="settings-section">
      <div class="section-title">Video &amp; Output</div>
      ${_folderRow('Video source', 'video_path',  cfg.video_path,  'Dashcam / onboard footage')}
      ${_folderRow('Export folder','export_path', cfg.export_path, 'Where exported videos are saved')}
    </section>

    <!-- RaceBox cloud -->
    <section class="settings-section">
      <div class="section-title">RaceBox Cloud</div>
      <p class="section-hint">Downloads sessions directly from racebox.pro. On first use a small login window opens — auth is saved for future downloads.</p>
      <div id="rb-setup-wrap">
        <div id="rb-pw-status" class="form-row" style="font-size:10px;color:var(--text3)">Checking…</div>
        <div class="form-row" id="rb-install-row">
          <button class="btn btn-secondary" id="rb-install-btn">Download Login Component</button>
          <span id="rb-install-msg" class="status-msg"></span>
        </div>
        <div id="rb-setup-log-wrap" class="hidden" style="margin-top:6px;">
          <textarea id="rb-setup-log" class="log-area" readonly
                    style="height:80px;font-size:10px;"
                    placeholder="Install log…"></textarea>
        </div>
      </div>
      <div id="rb-ready-wrap" class="hidden">
        <div class="form-row">
          <button class="btn btn-secondary" id="rb-login-btn">Check Auth</button>
          <button class="btn btn-accent btn-sm" id="rb-download-btn">⬇ Download New Sessions</button>
          <button class="btn btn-secondary btn-sm hidden" id="rb-cancel-btn">Cancel</button>
          <span id="rb-login-msg" class="status-msg"></span>
        </div>
      </div>
      <div id="rb-log-wrap" class="hidden" style="margin-top:8px;">
        <div class="progress-bar-wrap" style="margin-bottom:6px;">
          <div class="progress-bar-track">
            <div class="progress-bar-fill" id="rb-progress-fill" style="width:0%"></div>
          </div>
          <span class="progress-pct" id="rb-progress-pct">0%</span>
        </div>
        <textarea id="rb-log" class="log-area" readonly
                  style="height:120px;font-size:10px;"
                  placeholder="Download log will appear here…"></textarea>
      </div>
    </section>

    <!-- AIM Mychron DLL -->
    <section class="settings-section">
      <div class="section-title">AIM Mychron</div>
      <p class="section-hint">AIM .xrk / .xrz / .drk files are converted to CSV automatically on scan. This requires the MatLabXRK DLL provided free by AIM.</p>
      <div id="aim-dll-status" class="form-row" style="font-size:10px;color:var(--text3)">Checking…</div>
      <div class="form-row" style="margin-top:6px">
        <button class="btn btn-secondary" id="aim-dll-btn">Download DLL</button>
        <span id="aim-dll-msg" class="status-msg"></span>
      </div>
    </section>

    <!-- Encoder -->
    <section class="settings-section">
      <div class="section-title">Encoder</div>
      <p class="section-hint">OpenLap uses FFmpeg for video processing. These settings apply to every export.</p>
      <div class="form-row">
        <label>Codec</label>
        <select data-config-key="encoder" class="input-field">
          <option value="libx264" ${cfg.encoder === 'libx264' || !cfg.encoder ? 'selected' : ''}>H.264 (libx264) — Universal</option>
          <option value="libx265" ${cfg.encoder === 'libx265' ? 'selected' : ''}>H.265 (libx265) — Smaller files</option>
          <option value="h264_nvenc" ${cfg.encoder === 'h264_nvenc' ? 'selected' : ''}>H.264 NVENC — NVIDIA GPU</option>
          <option value="h264_videotoolbox" ${cfg.encoder === 'h264_videotoolbox' ? 'selected' : ''}>H.264 VideoToolbox — Apple</option>
        </select>
      </div>
      <div class="form-row">
        <label>Quality (CRF)</label>
        <div class="range-row">
          <input type="range" id="enc-crf" data-config-key="crf"
                 min="12" max="32" step="1" value="${cfg.crf ?? 18}">
          <span class="range-val" id="enc-crf-val">${cfg.crf ?? 18}</span>
        </div>
      </div>
      <div class="form-row">
        <label>Workers</label>
        <input type="number" data-config-key="workers" class="input-field input-narrow"
               value="${cfg.workers ?? 4}" min="1" max="16" step="1">
      </div>
      <div class="form-row" style="margin-top:8px">
        <button class="btn btn-secondary" id="enc-check-btn">Detect Encoders</button>
        <span id="enc-msg" class="status-msg"></span>
      </div>
      <div id="enc-results" class="enc-results hidden"></div>
    </section>

    <!-- Auto Sync -->
    <section class="settings-section">
      <div class="section-title">Auto Sync</div>
      <p class="section-hint">Automatically detect video-telemetry sync offset after each scan.
        Uses cross-correlation of G-force vs video motion (~20–60s per session).
        Only runs on sessions with no existing offset. Results are shown as "auto"
        in the Data tab — click Mark to confirm and promote to a user offset.</p>
      <div class="form-row">
        <label>Enable auto-sync on scan</label>
        <label class="toggle-switch">
          <input type="checkbox" data-config-key="auto_sync_enabled"
                 ${cfg.auto_sync_enabled ? 'checked' : ''}>
          <span class="toggle-thumb"></span>
        </label>
      </div>
    </section>

    <!-- About -->
    <section class="settings-section">
      <div class="section-title">About</div>
      <div class="about-row"><span class="about-key">Version</span><span class="about-val" id="about-version">—</span></div>
      <div class="about-row"><span class="about-key">Python</span><span class="about-val" id="about-python">—</span></div>
      <div class="about-row"><span class="about-key">Config</span>
        <span class="about-val" id="about-config" style="font-family:var(--mono); font-size:10px">—</span>
      </div>
    </section>

  </div><!-- /.settings-body -->
</div>`;
  }

  function _folderRow(label, key, value, hint) {
    return `
      <div class="sett-path-group">
        <div class="form-row">
          <label>${_esc(label)}</label>
          <div class="path-row">
            <input type="text" data-config-key="${key}" class="input-field"
                   value="${_esc(value || '')}" placeholder="Not configured"
                   style="font-family:var(--mono); font-size:10px;">
            <button class="btn btn-secondary btn-sm" data-browse-key="${key}">Browse…</button>
          </div>
        </div>
        ${hint ? `<div class="path-hint">${_esc(hint)}</div>` : ''}
      </div>`;
  }

  // ── Event wiring ──────────────────────────────────────────────────────────────

  function _bindEvents(container) {
    const $ = id => container.querySelector('#' + id);

    // Save button
    $('save-btn').addEventListener('click', () => _save(container));

    // Browse buttons
    container.querySelectorAll('[data-browse-key]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const path = await API.openFolderDialog();
        if (path) {
          const input = container.querySelector(`[data-config-key="${btn.dataset.browseKey}"]`);
          if (input) input.value = path;
        }
      });
    });

    // RaceBox playwright/chromium status
    function _refreshRbStatus() {
      API.raceboxPlaywrightStatus().then(s => {
        const statusEl   = $('rb-pw-status');
        const installRow = $('rb-install-row');
        const setupWrap  = $('rb-setup-wrap');
        const readyWrap  = $('rb-ready-wrap');
        if (!s || !s.playwright) {
          if (statusEl) statusEl.innerHTML = '<span style="color:var(--err)">● Browser engine not available in this build.</span>';
          if (installRow) installRow.classList.add('hidden');
          return;
        }
        if (!s.chromium) {
          if (statusEl) statusEl.innerHTML = '<span style="color:var(--warn)">○ Login component not installed — one-time ~130 MB download of Google Chromium (headless, only used to log into racebox.pro).</span>';
          if (installRow) installRow.classList.remove('hidden');
          if (setupWrap)  setupWrap.classList.remove('hidden');
          if (readyWrap)  readyWrap.classList.add('hidden');
        } else {
          if (statusEl) statusEl.innerHTML = '<span style="color:var(--ok)">● Login component ready.</span>';
          if (installRow) installRow.classList.add('hidden');
          if (setupWrap)  setupWrap.classList.remove('hidden');
          if (readyWrap)  readyWrap.classList.remove('hidden');
        }
      }).catch(() => {});
    }
    _refreshRbStatus();

    _unlistenFns.push(API.on('racebox_setup_log', detail => {
      const wrap = $('rb-setup-log-wrap');
      const ta   = $('rb-setup-log');
      if (wrap) wrap.classList.remove('hidden');
      if (ta)   { ta.value += (detail.message || '') + '\n'; ta.scrollTop = ta.scrollHeight; }
    }));
    _unlistenFns.push(API.on('racebox_setup_done', detail => {
      const ok = detail.ok !== false;
      _setMsg($('rb-install-msg'), detail.message || '', ok ? 'ok' : 'err');
      const btn = $('rb-install-btn');
      if (btn) btn.disabled = false;
      if (ok) _refreshRbStatus();
    }));

    $('rb-install-btn').addEventListener('click', () => {
      const btn = $('rb-install-btn');
      if (btn) btn.disabled = true;
      const ta = $('rb-setup-log');
      if (ta) ta.value = '';
      const logWrap = $('rb-setup-log-wrap');
      if (logWrap) logWrap.classList.remove('hidden');
      _setMsg($('rb-install-msg'), '', 'dim');
      API.installPlaywrightChromium();
    });

    // RaceBox auth check
    $('rb-login-btn').addEventListener('click', async () => {
      const btn   = $('rb-login-btn');
      const msgEl = $('rb-login-msg');
      btn.textContent       = 'Checking…';
      btn.disabled          = true;
      btn.style.color       = '';
      btn.style.borderColor = '';
      _setMsg(msgEl, '', 'dim');
      try {
        const result = await API.raceboxLogin('', '');
        if (result?.ok) {
          btn.textContent       = '✓ Auth OK';
          btn.style.color       = 'var(--ok)';
          btn.style.borderColor = 'var(--ok)';
        } else {
          btn.textContent = 'Check Auth';
          const notAvail = result?.error?.includes('Playwright') || result?.error?.includes('not available');
          _setMsg(msgEl, result?.error || 'Not authenticated.', notAvail ? 'dim' : 'warn');
        }
      } catch (e) {
        btn.textContent = 'Check Auth';
        _setMsg(msgEl, String(e), 'err');
      } finally {
        btn.disabled = false;
      }
    });

    // RaceBox download — subscribes to push events for live progress
    let _rbLogLines = [];

    function _rbSetProgress(pct, msg) {
      const fill  = $('rb-progress-fill');
      const pctEl = $('rb-progress-pct');
      if (fill)  fill.style.width   = Math.min(100, pct) + '%';
      if (pctEl) pctEl.textContent  = Math.min(100, Math.round(pct)) + '%';
    }

    function _rbAppendLog(msg) {
      _rbLogLines.push(msg);
      if (_rbLogLines.length > 200) _rbLogLines.shift();
      const ta = $('rb-log');
      if (ta) { ta.value = _rbLogLines.join('\n'); ta.scrollTop = ta.scrollHeight; }
    }

    function _rbSetDownloading(active) {
      const btn    = $('rb-download-btn');
      const cancel = $('rb-cancel-btn');
      if (btn)    btn.classList.toggle('hidden', active);
      if (cancel) cancel.classList.toggle('hidden', !active);
    }

    _unlistenFns.push(API.on('racebox_log', detail => {
      _rbAppendLog(detail.message || '');
    }));

    _unlistenFns.push(API.on('racebox_progress', detail => {
      _rbSetProgress(detail.value || 0, detail.message || '');
    }));

    _unlistenFns.push(API.on('racebox_done', detail => {
      _rbSetDownloading(false);
      const msgEl = $('rb-login-msg');
      const ok    = detail.ok !== false;
      const msg   = detail.message || (ok ? 'Done.' : 'Failed.');
      _setMsg(msgEl, msg, ok ? 'ok' : 'err');
      _rbAppendLog('── ' + msg);
      _rbSetProgress(ok ? 100 : 0);
    }));

    $('rb-download-btn').addEventListener('click', async () => {
      _rbLogLines = [];
      const ta = $('rb-log');
      if (ta) ta.value = '';
      _rbSetProgress(0);
      $('rb-log-wrap').classList.remove('hidden');
      _rbSetDownloading(true);
      _setMsg($('rb-login-msg'), '', 'dim');
      _rbAppendLog('Starting download… (a browser may open for first-time login)');
      await API.downloadRaceboxSessions();
    });

    $('rb-cancel-btn').addEventListener('click', async () => {
      await API.cancelRaceboxDownload();
      _rbSetDownloading(false);
      _rbAppendLog('Cancelling…');
    });

    // CRF slider label sync
    $('enc-crf').addEventListener('input', e => {
      $('enc-crf-val').textContent = e.target.value;
    });

    // Encoder detection
    $('enc-check-btn').addEventListener('click', async () => {
      const msgEl     = $('enc-msg');
      const resultsEl = $('enc-results');
      _setMsg(msgEl, 'Detecting…', 'dim');
      $('enc-check-btn').disabled = true;
      resultsEl.classList.add('hidden');

      try {
        const result = await API.checkEncoders();
        if (!result) {
          _setMsg(msgEl, 'FFmpeg not found.', 'err');
          return;
        }
        if (result.error) {
          _setMsg(msgEl, result.error, 'err');
          return;
        }

        _setMsg(msgEl, `FFmpeg ${result.version || 'found'}.`, 'ok');
        const encoders = result.encoders || [];
        resultsEl.innerHTML = encoders.map(e =>
          `<div class="enc-row">
             <span class="enc-name">${_esc(e.name)}</span>
             <span class="enc-label">${_esc(e.label)}</span>
             <span class="badge ${e.available ? 'badge-ok' : 'badge-muted'}">${e.available ? 'available' : 'unavailable'}</span>
           </div>`
        ).join('');
        resultsEl.classList.remove('hidden');
      } catch (err) {
        _setMsg(msgEl, String(err), 'err');
      } finally {
        $('enc-check-btn').disabled = false;
      }
    });

    // AIM DLL status + download
    function _refreshAimStatus() {
      API.aimDllStatus().then(r => {
        const el = $('aim-dll-status');
        if (!el) return;
        if (r && r.found) {
          el.innerHTML = '<span style="color:var(--ok)">● MatLabXRK DLL found — AIM XRK conversion available.</span>';
        } else {
          el.innerHTML = '<span style="color:var(--text3)">○ MatLabXRK DLL not found — AIM XRK conversion unavailable.</span>';
        }
      }).catch(() => {});
    }
    _refreshAimStatus();

    _unlistenFns.push(API.on('aim_dll_progress', detail => {
      _setMsg($('aim-dll-msg'), detail.message || '', 'dim');
    }));
    _unlistenFns.push(API.on('aim_dll_done', detail => {
      const ok = detail.ok !== false;
      _setMsg($('aim-dll-msg'), detail.message || '', ok ? 'ok' : 'err');
      $('aim-dll-btn').disabled = false;
      if (ok) _refreshAimStatus();
    }));

    $('aim-dll-btn').addEventListener('click', () => {
      $('aim-dll-btn').disabled = true;
      _setMsg($('aim-dll-msg'), 'Downloading…', 'dim');
      API.downloadAimDll();
    });

    // Populate about section
    API.getAboutInfo().then(info => {
      if (!info) return;
      const verEl = $('about-version');
      const pyEl  = $('about-python');
      const cfgEl = $('about-config');
      if (verEl && info.version) verEl.textContent = info.version;
      if (pyEl  && info.python)  pyEl.textContent  = info.python;
      if (cfgEl && info.config)  cfgEl.textContent = info.config;
    }).catch(() => {});
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────

  async function _save(container) {
    const updated = { ..._config };
    const _intKeys  = new Set(['crf', 'workers']);
    const _boolKeys = new Set(['auto_sync_enabled']);
    container.querySelectorAll('[data-config-key]').forEach(el => {
      const key = el.dataset.configKey;
      if (_boolKeys.has(key) || el.type === 'checkbox') {
        updated[key] = el.checked;
      } else {
        updated[key] = _intKeys.has(key) ? (parseInt(el.value, 10) || 0) : el.value.trim();
      }
    });
    // Persist email (never password — that goes through the login flow only)
    const emailEl = container.querySelector('#rb-email');
    if (emailEl) updated.racebox_email = emailEl.value.trim();

    await API.saveConfig(updated);
    _config = updated;

    const msg = container.querySelector('#save-msg');
    if (msg) {
      _setMsg(msg, 'Saved.', 'ok');
      setTimeout(() => { if (msg) msg.textContent = ''; }, 2000);
    }
  }

  function _setMsg(el, text, variant) {
    if (!el) return;
    el.textContent = text;
    el.className   = 'status-msg status-' + variant;
  }

  function _esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  Router.register('settings', { mount, unmount });
})();
