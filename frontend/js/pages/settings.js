/**
 * settings.js — Settings page (Phase 4).
 * Functional minimal implementation: folder configuration + save.
 */
(function () {
  let _config = null;

  function pathRow(label, key, value, description) {
    return `
      <div class="form-row" style="margin-bottom:12px; align-items:flex-start; flex-direction:column; gap:6px;">
        <div style="display:flex; align-items:center; justify-content:space-between; width:100%">
          <span class="form-label" style="min-width:unset">${label}</span>
          <button class="btn btn-sm" data-browse-key="${key}">Browse…</button>
        </div>
        <input type="text" value="${value || ''}" data-config-key="${key}"
               style="width:100%; font-family:var(--mono); font-size:10px;"
               placeholder="Not configured">
        ${description ? `<div style="font-size:9px; color:var(--text3)">${description}</div>` : ''}
      </div>`;
  }

  async function save(container) {
    const updated = { ..._config };
    container.querySelectorAll('[data-config-key]').forEach(el => {
      updated[el.dataset.configKey] = el.value.trim();
    });
    await API.saveConfig(updated);
    _config = updated;
    const msg = container.querySelector('#save-msg');
    if (msg) {
      msg.textContent = 'Saved.';
      msg.style.color = 'var(--ok)';
      setTimeout(() => { if (msg) msg.textContent = ''; }, 2000);
    }
  }

  async function mount(container) {
    _config = await API.getConfig();

    container.innerHTML = `
      <div class="page">
        <div class="toolbar">
          <div class="toolbar-left">
            <span class="page-title">Settings</span>
          </div>
          <div class="toolbar-right">
            <span id="save-msg" style="font-size:10px"></span>
            <button class="btn btn-accent" id="save-btn">Save</button>
          </div>
        </div>
        <div class="page-divider"></div>

        <div style="padding:24px; max-width:680px">
          <div class="card" style="margin-bottom:16px">
            <div style="font-size:11px; font-weight:700; color:var(--text2);
                        text-transform:uppercase; letter-spacing:0.05em; margin-bottom:14px">
              Telemetry Folders
            </div>
            ${pathRow('RaceBox',    'racebox_path', _config.racebox_path, 'RaceBox CSV files')}
            ${pathRow('AIM Mychron','aim_path',     _config.aim_path,     'AIM XRK/CSV files')}
            ${pathRow('MoTeC',      'motec_path',   _config.motec_path,   'MoTeC .ld files')}
            ${pathRow('GPX',        'gpx_path',     _config.gpx_path,     '.gpx track files')}
            ${pathRow('Video',      'video_path',   _config.video_path,   'Dashcam / onboard video')}
            ${pathRow('Export',     'export_path',  _config.export_path,  'Where exported videos are saved')}
          </div>
        </div>
      </div>`;

    container.querySelector('#save-btn').addEventListener('click', () => save(container));

    // Browse buttons
    container.querySelectorAll('[data-browse-key]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const key  = btn.dataset.browseKey;
        const path = await API.openFolderDialog();
        if (path) {
          const input = container.querySelector(`[data-config-key="${key}"]`);
          if (input) input.value = path;
        }
      });
    });
  }

  function unmount() {}

  Router.register('settings', { mount, unmount });
})();
