# OpenLap

**OpenLap** is an open-source desktop application that overlays telemetry data on top of racing video footage. It is data-source agnostic — RaceBox is supported today, with MoTeC and others planned.

> Licensed under the GNU General Public License v3. Free forever, forks must stay free.

---

## Features

- Match telemetry sessions to video files automatically
- Align telemetry and video with a frame-accurate sync tool
- Drag-and-resize overlay elements directly on a video preview
- Plugin-based style system — drop a `.py` file into `styles/` and it appears in the UI
- Export single laps, full sessions, or all laps in one click
- GPU-accelerated encoding (NVENC/VAAPI/VideoToolbox) with auto-detection
- Persistent sync offsets — set once, remembered forever per session

---

## Screenshots

_Coming soon_

---

## Installation

### Requirements

- Python 3.10+
- FFmpeg on your system `PATH`
- The following Python packages:

```
pip install opencv-python pillow numpy matplotlib
```

For RaceBox download support (optional):

```
pip install playwright
playwright install chromium
```

### Running

Double-click `openlap.pyw`, or from a terminal:

```bash
python openlap.pyw
```

---

## Usage

### 1. Settings tab
Set your **telemetry folder**, **video folder**, and **export folder** once. These are remembered between sessions.

### 2. Data tab
Click **Scan** (or it runs automatically on startup) to discover all sessions and match them with video files.

Select a session and use the **Align Video** panel to sync telemetry with footage:
- Scrub to the moment the lap starts
- Press **M** (or the Mark button) to lock the offset
- The offset is saved immediately and restored on next scan

### 3. Export tab
- Drag and resize the **Map** and **Telemetry** overlay boxes on the video preview
- Pick a style for each element from the dropdowns
- Choose export scope: Fastest Lap, Full Session, or All Laps
- Click **Export**

---

## Project Structure

```
openlap.pyw             Entry point — launch this to start the app

app_shell.py            Main window: 3-tab sidebar, queue dispatcher, shared state
app_config.py           Persistent config (paths, sync offsets, overlay layout)
                        Saved to ~/.openlap/config.json

design_tokens.py        Colours, fonts, spacing constants
widgets.py              Shared UI primitives (Divider, etc.)

page_data.py            Data tab — session tree, video matching, sync panel
page_export.py          Export tab — overlay editor, style pickers, export controls
page_settings.py        Settings tab — folder paths, RaceBox download, encoder info

overlay_editor.py       Drag-and-resize canvas widget (letterbox-aware)
overlay_worker.py       Per-frame render worker (called by multiprocessing pool)
overlay_utils.py        Shared helpers: blend_rgba, fig_to_rgba, dummy data generators

video_renderer.py       render_lap(), concat_videos(), detect_encoder()

session_scanner.py      Scan folders, match CSV sessions to video files
racebox_data.py         Parse RaceBox CSV files into Session/Lap objects
racebox_downloader.py   Download new sessions from RaceBox cloud (Playwright)

style_registry.py       Discover, load and call style plugins from styles/

styles/
  map_circuit.py        Map style: circuit trace with current position dot
  telemetry_strip.py    Telemetry style: horizontal strip (speed, G-force, timer)
  telemetry_gforce.py   Telemetry style: G-force crosshair with fading trail
  speedo.py             Telemetry style: speedometer arc gauge
```

---

## Writing a Custom Style

Create a file in the `styles/` folder. It needs three things:

```python
STYLE_NAME   = "My Style"       # shown in the UI dropdown
ELEMENT_TYPE = "telemetry"      # "telemetry" or "map"

def render(data: dict, w: int, h: int) -> np.ndarray:
    """Return an RGBA numpy array of shape (h, w, 4)."""
    ...
```

### `data` keys for `telemetry` styles

| Key | Type | Description |
|---|---|---|
| `speed_history` | `list[float]` | Speed values (km/h), most recent last |
| `gx_history` | `list[float]` | Lateral G-force history |
| `gy_history` | `list[float]` | Longitudinal G-force history |
| `speed` | `float` | Current speed (km/h) |
| `gx` | `float` | Current lateral G |
| `gy` | `float` | Current longitudinal G |
| `lean` | `float` | Lean angle in degrees (bikes only) |
| `lap_time` | `float` | Elapsed lap time in seconds |
| `lap_duration` | `float` | Total lap duration in seconds |
| `is_bike` | `bool` | True if session is a motorbike |

### `data` keys for `map` styles

| Key | Type | Description |
|---|---|---|
| `lats` | `list[float]` | Latitude points for the full track |
| `lons` | `list[float]` | Longitude points for the full track |
| `cur_idx` | `int` | Index of the current position in the track |

Drop the file into `styles/` and restart the app — it appears in the style picker immediately. No registration needed.

---

## Data Sources

| Source | Status |
|---|---|
| RaceBox | Supported |
| MoTeC | Coming soon |

---

## Contributing

Pull requests are welcome. Please open an issue first for larger changes so we can discuss the approach.

This project is licensed under the **GNU General Public License v3**. Any fork or derivative must also be open source and remain free to use.
