# OpenLap — Todo

## Build & distribution
- [ ] Verify `pyinstaller OpenLap.spec --clean -y` produces a working `dist/OpenLap/OpenLap.exe` locally
- [ ] Add `frontend/icon.ico` and uncomment the icon line in `OpenLap.spec`
- [x] GitHub Actions workflow (`.github/workflows/release.yml`) — builds exe and publishes to GitHub Releases on `v*` tag push

## Features / improvements
- [ ] RaceBox cloud download (requires `playwright` optional dep) — verify still works after rewrite
- [ ] MoTeC .ld parser — validate against real GT3 car logs
