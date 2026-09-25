/**
 * export_params.js — builds the params object passed to API.startExport().
 *
 * Extracted as a standalone module (rather than living inline in editor.js,
 * which is where exports are now triggered from) so it can be unit-tested
 * without mounting editor.js's full video/canvas page lifecycle.
 */
function buildExportParams({ items, cfg, layout }) {
  cfg    = cfg    || {};
  layout = layout || {};
  return {
    items,
    // Call-level fallbacks — only used for queue items that predate per-item
    // scope/padding/overlay_only (see export_runner.py's item.get(...) or ... fallback).
    scope:            'selected_lap',
    padding:          5.0,
    clip_start_s:     0,
    clip_end_s:       0,
    overlay_only:     false,
    ref_mode:         layout.ref_mode         || 'none',
    ref_lap_csv_path: layout.ref_lap_csv_path || '',
    ref_lap_num:      layout.ref_lap_num      || 0,
    encoder:          cfg.encoder             || 'libx264',
    crf:              cfg.crf                 ?? 18,
    workers:          cfg.workers             ?? 4,
    speed_unit:       cfg.speed_unit          || 'auto',
    output_height:    cfg.output_height       || 0,
    output_fps:       cfg.output_fps          || 0,
    bitrate_kbps:     cfg.bitrate_kbps        || 0,
    is_bike:          layout.is_bike          ?? false,
    show_map:         layout.show_map         ?? true,
    show_tel:         layout.show_tel         ?? true,
    export_path:      cfg.export_path         || '',
    layout,
  };
}

// Export as a namespace object (no ES module build step required) — mirrors gauges/base.js
const ExportParams = { buildExportParams };
