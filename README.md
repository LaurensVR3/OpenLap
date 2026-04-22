# OpenLap — Free Motorsport Telemetry Overlay Software

**OpenLap** is a free, open-source desktop application that overlays telemetry data on racing video footage. It supports **RaceBox**, **AIM MyChron**, **MoTeC**, and **GPX** data sources and runs entirely on your PC — no subscription, no cloud, no fees.

> A free alternative to RaceRender, Video VBOX, and TrackAddict for Windows.

Point it at your telemetry files and a folder of race videos, and it matches sessions, syncs timing, and renders professional gauge overlays — all from a single window.

> Licensed under the **GNU General Public License v3**. Free forever. Forks must stay open source.

---

## Quick Start (Windows — no technical knowledge needed)

1. **Download** the latest release: **[⬇ OpenLap for Windows](https://github.com/LaurensVR3/OpenLap/releases/latest)**
2. **Unzip** the downloaded `.zip` file anywhere you like (e.g. your Desktop or `C:\Tools\OpenLap`)
3. **Run** `OpenLap.exe` — Windows may show a SmartScreen warning the first time; click **More info → Run anyway** (the app is open source and safe)
4. **Settings tab** — set the folders where your telemetry files live (RaceBox CSV, AIM `.xrk`, MoTeC `.ld`, or GPX) and your video folder
   - *AIM users:* click **Download DLL** the first time — this fetches the conversion library automatically
   - *RaceBox cloud users:* click **Download Login Component**, wait for it to finish, then **Check Auth**
5. **Data tab** — sessions are scanned automatically; click **▶** next to a session to open it in the editor
6. **Overlay tab** — drag gauges onto the video preview, pick a theme, adjust styles
7. **Export tab** — choose quality and encoding, then **Start Export** — finished videos are saved to your Export Folder

> **Important:** keep `OpenLap.exe` and the `_internal` folder in the same directory — they must stay together.

---

## Preview

**Sample output video** — Karting Haute Picardie Arvillers:

[![OpenLap telemetry overlay on karting video — speed, RPM, G-force, circuit map gauges](https://img.youtube.com/vi/gsKdIWs6FvM/maxresdefault.jpg)](https://youtu.be/gsKdIWs6FvM)

### Screenshots

| Data tab | Export tab | Settings tab |
|---|---|---|
| ![Data tab — session list with lap times and sync status](docs/screenshot_data.png) | ![Export tab — encoder selection and progress log](docs/screenshot_export.png) | ![Settings tab — telemetry and video folder configuration](docs/screenshot_settings.png) |

---

## Features

### Data & Session Management
- Per-source telemetry folders — configure separate directories for RaceBox, AIM, MoTeC, and GPX data
- Auto-scan on startup with persistent session cache for fast restarts
- Sessions grouped by date with lap list, best time, and video match status
- Manual video reassignment for sessions where auto-matching doesn't find the right clip
- Multi-clip support — multiple video segments per session are joined automatically before rendering
- **Auto-sync** (opt-in): cross-correlates video motion against G-force to detect the sync offset automatically after each scan — results appear as `~ auto` and can be confirmed or fine-tuned in the Data tab
- Frame-accurate manual sync: scrub the video preview to where the lap timer starts, press **Mark** — saves as a `✓ user` offset that auto-sync will never overwrite
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

| Source | Devices / File types | Notes |
|---|---|---|
| **RaceBox** | RaceBox Mini, Mini S, Pro, Bike (`.csv`) | Car and bike mode; cloud download built-in |
| **AIM MyChron** | MyChron 5, MyChron 5S, Solo 2 (`.xrk` · `.xrz` · `.drk`) | Auto-converted to CSV on scan |
| **MoTeC** | Any MoTeC logger exporting `.ld` | Binary i2 format; full session lap timing |
| **GPX** | Any GPS device or phone app (`.gpx`) | Speed derived from position + timestamp |

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

## Why OpenLap?

Most telemetry overlay tools are expensive, subscription-based, or locked to a single data source. OpenLap is:

- **Free** — no licence fees, no watermarks, no export limits
- **Open source** — GPL v3; inspect, modify, and contribute
- **Multi-source** — RaceBox, AIM MyChron, MoTeC, and GPX in one app
- **GPU-accelerated** — NVIDIA NVENC, AMD AMF, Intel QSV; renders fast on any modern PC
- **Offline** — no internet required after initial setup; your data stays on your machine

Common use cases: karting, circuit racing, track days, hillclimb, motorcycle track riding, autocross / autosolo.

---

## Run from source

**Requirements**

- Python 3.10+
- FFmpeg available on your system `PATH`

**Install Python dependencies**

```bash
pip install -e .
```

For RaceBox cloud download (optional):

```bash
pip install -e ".[racebox-download]"
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
- **AIM MyChron** — point at the folder containing `.xrk` files; conversion to CSV happens automatically
- **MoTeC** — point at the folder containing `.ld` files
- **GPX** — point at the folder containing `.gpx` files
- **Auto Sync** — enable to automatically detect sync offsets after each scan (~20–60s per session using G-force cross-correlation); off by default

### 2. Data tab

Sessions are scanned automatically on startup. Click **Scan** to refresh.

- Sessions appear grouped by date; click a row to see its detail and sync panel
- The **Sync** column shows the status of each session:
  - `≈ unset` — no offset set yet
  - `~ auto` — offset detected automatically (blue); scrub to verify, click **Confirm** to lock it in
  - `✓ user` — offset manually confirmed (green)
  - `no vid` — no matching video found
- Use the **Align Video** panel to sync manually: scrub to where the lap timer starts, press **Mark**
- Use **Browse for video…** to manually link a session to a video file
- Click **Open in Overlay →** to jump to the editor with this session loaded

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
