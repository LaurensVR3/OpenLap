# OpenLap

**OpenLap** is an open-source desktop application that overlays telemetry data on top of racing video footage. It is data-source agnostic — RaceBox and AIM Mychron are supported today, with MoTeC and others planned.

> Licensed under the GNU General Public License v3. Free forever, forks must stay free.

---

## Features

- Match telemetry sessions to video files automatically
- Align telemetry and video with a frame-accurate sync tool
- Individual, draggable gauge overlays per channel (Speed, RPM, G-Force, Lean Angle, etc.)
- Multiple gauge styles per channel — Numeric, Bar, Dial, Line, Lean
- Save, load, and delete named overlay preset layouts
- Plugin-based style system — drop a `.py` file into `styles/` and it appears in the UI
- Export single laps, full sessions, or all laps in one click
- GPU-accelerated encoding (NVENC/VAAPI/VideoToolbox) with auto-detection
- Persistent sync offsets — set once, remembered forever per session
- Manual multi-clip video assignment per session

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

- AIM Mychron `.XRK` files are automatically converted to CSV on scan
- Click a session to select it and view its laps
- Use **Reassign Video** to manually link a session to a specific video file

Select a session and use the **Align Video** panel to sync telemetry with footage:
- Scrub to the moment the lap starts
- Press **M** (or the Mark button) to lock the offset
- The offset is saved immediately and restored on next scan

### 3. Export tab
- Add gauge elements from the **Add Gauge** panel — pick a channel and style for each
- Drag and resize each gauge and the **Map** overlay on the video preview
- Save the current layout as a named **preset**, or load/delete existing presets
- Choose export scope: Fastest Lap, Full Session, or All Laps
- Click **Export**

---

## Project Structure

```
openlap.pyw             Entry point — launch this to start the app

app_shell.py            Main window: 3-tab sidebar, queue dispatcher, shared state
app_config.py           Persistent config (paths, sync offsets, overlay layout, presets)
                        Saved to ~/.openlap/config.json

design_tokens.py        Colours, fonts, spacing constants
widgets.py              Shared UI primitives (Divider, etc.)

page_data.py            Data tab — session tree, video matching, sync panel
page_export.py          Export tab — gauge editor, preset management, export controls
page_settings.py        Settings tab — folder paths, RaceBox download, encoder info

overlay_editor.py       Drag-and-resize canvas widget (letterbox-aware)
overlay_worker.py       Per-frame render worker (called by multiprocessing pool)
overlay_utils.py        Shared helpers: blend_rgba, fig_to_rgba, dummy data generators

gauge_channels.py       Channel metadata and data-builder for the gauge system

video_renderer.py       render_lap(), concat_videos(), detect_encoder()

session_scanner.py      Scan folders, match sessions to video files, auto-convert XRK
racebox_data.py         Parse RaceBox CSV files into Session/Lap objects
racebox_downloader.py   Download new sessions from RaceBox cloud (Playwright)
aim_data.py             Parse AIM Mychron CSV files into Session/Lap objects
xrk_to_csv.py           Convert AIM .XRK binary files to CSV (RPM, exhaust temp, etc.)

style_registry.py       Discover, load and call style plugins from styles/

styles/
  map_circuit.py        Map style: circuit trace with current position dot
  gauge_numeric.py      Gauge style: plain numeric readout
  gauge_bar.py          Gauge style: horizontal/vertical bar
  gauge_dial.py         Gauge style: arc dial
  gauge_line.py         Gauge style: scrolling line graph
  gauge_lean.py         Gauge style: lean-angle indicator (bike mode only)
```

---

## Writing a Custom Gauge Style

Create a file in the `styles/` folder named `gauge_<name>.py`. It needs:

```python
STYLE_NAME   = "My Style"   # shown in the UI dropdown
ELEMENT_TYPE = "gauge"      # must be "gauge"

def render(data: dict, w: int, h: int) -> np.ndarray:
    """Return an RGBA numpy array of shape (h, w, 4)."""
    ...
```

### `data` keys for `gauge` styles

| Key | Type | Description |
|---|---|---|
| `channel` | `str` | Channel identifier (e.g. `"speed"`, `"rpm"`) |
| `value` | `float` | Current value |
| `history_vals` | `list[float]` | Recent history, most recent last |
| `label` | `str` | Human-readable channel name |
| `unit` | `str` | Unit string (e.g. `"km/h"`, `"°C"`) |
| `min_val` | `float` | Expected minimum for scaling |
| `max_val` | `float` | Expected maximum for scaling |
| `symmetric` | `bool` | True if the range is centred on zero (e.g. G-force) |

### Available channels

| Channel | Label | Unit |
|---|---|---|
| `speed` | Speed | km/h |
| `rpm` | RPM | rpm |
| `exhaust_temp` | Exhaust Temp | °C |
| `gforce_lon` | Long G | G |
| `gforce_lat` | Lat G | G |
| `lean` | Lean | ° |
| `lap_time` | Lap Time | s |

Drop the file into `styles/` and restart the app — it appears in the style picker immediately. No registration needed.

### Writing a Custom Map Style

Map styles work the same way but use `ELEMENT_TYPE = "map"` and receive different data:

| Key | Type | Description |
|---|---|---|
| `lats` | `list[float]` | Latitude points for the full track |
| `lons` | `list[float]` | Longitude points for the full track |
| `cur_idx` | `int` | Index of the current position in the track |

---

## Data Sources

| Source | Status |
|---|---|
| RaceBox | Supported |
| AIM Mychron (XRK) | Supported |
| MoTeC | Coming soon |

---

## Contributing

Pull requests are welcome. Please open an issue first for larger changes so we can discuss the approach.

This project is licensed under the **GNU General Public License v3**. Any fork or derivative must also be open source and remain free to use.
