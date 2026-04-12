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
  let _config = null;

  // ── Mount / Unmount ──────────────────────────────────────────────────────────

  async function mount(container) {
    _config = await API.getConfig();
    container.innerHTML = _buildHTML(_config);
    _bindEvents(container);
  }

  function unmount() {}

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
      <p class="section-hint">Optional — log in to download sessions directly from RaceBox servers.</p>
      <div class="form-row">
        <label>Email</label>
        <input type="text" id="rb-email" class="input-field" placeholder="you@example.com"
               value="${_esc(cfg.racebox_email || '')}">
      </div>
      <div class="form-row">
        <label>Password</label>
        <input type="password" id="rb-password" class="input-field" placeholder="••••••••">
      </div>
      <div class="form-row">
        <button class="btn btn-secondary" id="rb-login-btn">Test Login</button>
        <span id="rb-login-msg" class="status-msg"></span>
      </div>
    </section>

    <!-- Encoder -->
    <section class="settings-section">
      <div class="section-title">Encoder</div>
      <p class="section-hint">OpenLap uses FFmpeg for video processing. Click below to detect available hardware encoders.</p>
      <div class="form-row">
        <button class="btn btn-secondary" id="enc-check-btn">Detect Encoders</button>
        <span id="enc-msg" class="status-msg"></span>
      </div>
      <div id="enc-results" class="enc-results hidden"></div>
    </section>

    <!-- About -->
    <section class="settings-section">
      <div class="section-title">About</div>
      <div class="about-row"><span class="about-key">Version</span><span class="about-val">0.1.0</span></div>
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

    // RaceBox login test
    $('rb-login-btn').addEventListener('click', async () => {
      const email    = $('rb-email').value.trim();
      const password = $('rb-password').value;
      const msgEl    = $('rb-login-msg');

      if (!email || !password) {
        _setMsg(msgEl, 'Enter email and password.', 'warn');
        return;
      }

      _setMsg(msgEl, 'Testing…', 'dim');
      $('rb-login-btn').disabled = true;

      try {
        const result = await API.raceboxLogin(email, password);
        if (result && result.ok) {
          _setMsg(msgEl, 'Login successful.', 'ok');
        } else {
          _setMsg(msgEl, result.error || 'Login failed.', 'err');
        }
      } catch (e) {
        _setMsg(msgEl, String(e), 'err');
      } finally {
        $('rb-login-btn').disabled = false;
      }
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

    // Populate about section
    API.getAboutInfo().then(info => {
      if (!info) return;
      const pyEl  = $('about-python');
      const cfgEl = $('about-config');
      if (pyEl  && info.python)  pyEl.textContent  = info.python;
      if (cfgEl && info.config)  cfgEl.textContent = info.config;
    }).catch(() => {});
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────

  async function _save(container) {
    const updated = { ..._config };
    container.querySelectorAll('[data-config-key]').forEach(el => {
      updated[el.dataset.configKey] = el.value.trim();
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
