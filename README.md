# OpenLap

**OpenLap** is an open-source desktop application that overlays telemetry data on racing video footage. Point it at your telemetry files and a folder of videos, and it matches sessions, syncs timing, and renders professional-looking gauge overlays — all from a single window.

> Licensed under the **GNU General Public License v3**. Free forever. Forks must stay open source.

---

## Preview

**Sample output video** — Karting Haute Picardie Arvillers:

[![OpenLap sample output](https://img.youtube.com/vi/0XuByCyL_mA/maxresdefault.jpg)](https://www.youtube.com/watch?v=0XuByCyL_mA)

---

## Features

### Data & Session Management
- Per-source telemetry folders — configure separate directories for RaceBox, AIM, MoTeC, and GPX data
- Auto-scan on startup with persistent session cache for fast restarts
- Sessions grouped by date with lap list, best time, and video match status
- Manual video reassignment for sessions where auto-matching doesn't find the right clip
- Multi-clip support — multiple video segments per session are joined automatically before rendering
- Frame-accurate sync tool: scrub the video preview to where the lap timer starts, press **Mark** — offset is saved per file and never needs re-entering
- RaceBox cloud download directly from the app (requires a RaceBox account)
- AIM `.xrk` / `.xrz` / `.drk` files are converted to CSV on first scan using the AIM MatLabXRK DLL

### Overlay Editor
- Live video preview with scrub bar — see exactly how gauges look on your footage before exporting
- Freely positionable, resizable gauge elements — drag to move, drag corner handle to resize
- Element-to-element snapping with cyan alignment guides; size snaps to 5% grid
- Lap selector — switch between laps while the video preview stays in sync
- **4 overlay themes**: Dark · Light · Colorful · Monochrome
- **Gauge styles**: Numeric · Bar · Dial · Line · Delta · Compare · Lean · G-Meter · Splits · Sector Bar · Multi-Line · Circuit Map · Zoomed Map · Scoreboard · Info · Image/Logo
- Bike mode — enables Lean gauge and reads lean angle from compatible devices
- Reference lap overlay — compare any lap against a reference with live delta time
- Named preset layouts — save, load, and switch overlay configurations

### Export
- **Scope**: This Lap, Fastest Lap, All Laps (one file per lap), or Full Session
- GPU-accelerated encoding with auto-detection: NVENC (NVIDIA) · AMF (AMD) · QSV (Intel) · libx264 (CPU fallback)
- Adjustable quality (CRF) and parallel worker count
- Configurable pre/post lap padding
- Progress bar and log output per render job

### Extensibility
- Plugin-based style system — drop a `.py` file into `styles/` and it appears in the UI automatically
- All styles receive theme colour tokens; custom styles support all four themes with no extra work

---

## Supported Data Sources

| Source | File types | Notes |
|---|---|---|
| **RaceBox** | `.csv` (RaceBox format) | Car and bike mode |
| **AIM Mychron** | `.xrk` · `.xrz` · `.drk` | Auto-converted to CSV on scan |
| **MoTeC** | `.ld` | Binary i2 format; full session lap timing |
| **GPX** | `.gpx` | GPS track files; speed derived from position + timestamp |

### Telemetry channels

| Channel | Label | Unit |
|---|---|---|
| `speed` | Speed | km/h |
| `rpm` | RPM | rpm |
| `exhaust_temp` | Exhaust Temp | °C |
| `gforce_lon` | Long G | G |
| `gforce_lat` | Lat G | G |
| `lean` | Lean Angle | ° |
| `altitude` | Altitude | m |
| `lap_time` | Lap Time | s |
| `delta_time` | Delta | s |

---

## Installation

### Option A — Pre-built Windows executable

Download the latest release from the [Releases](../../releases) page and run `OpenLap.exe`. No Python or dependencies required.

### Option B — Run from source

**Requirements**

- Python 3.10+
- FFmpeg available on your system `PATH`

**Install Python dependencies**

```bash
pip install pywebview opencv-python pillow numpy
```

For RaceBox cloud download (optional):

```bash
pip install playwright
playwright install chromium
```

**Run**

```bash
python main.py
```

Configuration is stored at `~/.openlap/config.json`.

---

## Usage

### 1. Settings tab

Configure folders for each telemetry source, your **Video Folder**, and **Export Folder**.

- **RaceBox** — set the folder where CSVs are stored; log in to download new sessions from the cloud
- **AIM Mychron** — point at the folder containing `.xrk` files; conversion to CSV happens automatically
- **MoTeC** — point at the folder containing `.ld` files
- **GPX** — point at the folder containing `.gpx` files

### 2. Data tab

Sessions are scanned automatically on startup. Click **Scan** to refresh.

- Sessions appear grouped by date; click a row to expand the lap list
- Click **▶** on a session to open it in the Overlay editor
- Use the **Sync** panel to align video with telemetry: scrub to where the lap timer starts, press **Mark**
- Use **Reassign Video** to manually link a session to a video file

### 3. Overlay tab

- The video preview shows the current session; use the lap selector (◀ ▶ or dropdown) to switch laps
- **Add Gauge** to place a new element — choose channel and style
- Drag elements to reposition; drag the corner handle to resize
- Switch themes and save layouts as named presets
- Set a **Reference Lap** to enable delta time and reference lap comparison
- Click **+ Export** to queue the current lap for export

### 4. Export tab

- Review the queued laps (each has its own ✕ to remove individually)
- Choose encoder, quality, padding, and worker count
- Click **Start Export**; progress and log are shown live

---

## Building from source (Windows)

```bash
pip install pyinstaller
pyinstaller OpenLap.spec --clean -y
```

The executable and all dependencies are output to `dist/OpenLap/`.

---

## License

GNU General Public License v3 — see [LICENSE](LICENSE) for details.
