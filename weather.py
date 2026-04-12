"""
weather.py — Open-Meteo historical weather lookup (free, no API key required)
"""
from __future__ import annotations
import json
import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = Path.home() / '.openlap' / 'weather_cache.json'

# WMO Weather Interpretation Codes → short description
_WMO: dict[int, str] = {
    0:  'Clear sky',
    1:  'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
    45: 'Fog', 48: 'Icy fog',
    51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
    56: 'Freezing drizzle', 57: 'Heavy freezing drizzle',
    61: 'Light rain', 63: 'Rain', 65: 'Heavy rain',
    66: 'Freezing rain', 67: 'Heavy freezing rain',
    71: 'Light snow', 73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains',
    80: 'Rain showers', 81: 'Rain showers', 82: 'Violent showers',
    85: 'Snow showers', 86: 'Heavy snow showers',
    95: 'Thunderstorm', 96: 'Thunderstorm + hail', 99: 'Thunderstorm + hail',
}

_COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def _compass(deg: float) -> str:
    """Convert wind direction in degrees to an 8-point compass label."""
    return _COMPASS[round(float(deg) / 45) % 8]


def fetch_weather(lat: float, lon: float, date_utc_iso: str) -> tuple[str, str]:
    """
    Return (weather_str, wind_str) for the given location + UTC datetime.

    weather_str: e.g. "18°C  Partly cloudy"
    wind_str:    e.g. "NW  12 km/h"

    Both are '' on any failure (no GPS, no network, API error, etc.).
    """
    if not lat or not lon or not date_utc_iso:
        return '', ''
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(date_utc_iso.replace('Z', '+00:00'))
        date_str = dt.strftime('%Y-%m-%d')
        hour = dt.hour
        days_ago = (datetime.now(timezone.utc) - dt).days
    except Exception:
        return '', ''

    # Round coordinates to ~1 km for cache key
    lat_r = round(float(lat), 2)
    lon_r = round(float(lon), 2)
    cache_key = f'{lat_r},{lon_r},{date_str}'

    cache = _load_cache()
    if cache_key in cache:
        return _format(cache[cache_key], hour)

    hourly = _fetch_hourly(lat_r, lon_r, date_str, days_ago)
    if hourly:
        cache[cache_key] = hourly
        _save_cache(cache)
        return _format(hourly, hour)
    return '', ''


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_hourly(lat: float, lon: float, date_str: str, days_ago: int) -> dict:
    """Try archive API first; fall back to forecast API for recent sessions."""
    params = (
        f'latitude={lat}&longitude={lon}'
        f'&start_date={date_str}&end_date={date_str}'
        f'&hourly=temperature_2m,weathercode,windspeed_10m,winddirection_10m'
        f'&timezone=UTC'
    )
    urls = []
    if days_ago > 5:
        urls.append(f'https://archive-api.open-meteo.com/v1/archive?{params}')
    else:
        # Archive may not have today/recent data; try forecast first
        past = min(max(days_ago + 1, 1), 92)
        urls.append(
            f'https://api.open-meteo.com/v1/forecast?{params}'
            f'&past_days={past}&forecast_days=1'
        )
        urls.append(f'https://archive-api.open-meteo.com/v1/archive?{params}')

    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=6) as resp:
                data = json.loads(resp.read())
            hourly = data.get('hourly', {})
            if hourly.get('temperature_2m'):
                return hourly
        except Exception as exc:
            logger.debug('Weather fetch %s failed: %s', url, exc)
    return {}


def _format(hourly: dict, hour: int) -> tuple[str, str]:
    """Return (weather_str, wind_str) from an hourly data dict."""
    try:
        temps  = hourly.get('temperature_2m',   [])
        codes  = hourly.get('weathercode',       [])
        speeds = hourly.get('windspeed_10m',     [])
        dirs   = hourly.get('winddirection_10m', [])

        if not temps or hour >= len(temps):
            return '', ''

        temp = temps[hour]
        code = int(codes[hour])  if codes  and hour < len(codes)  else None
        spd  = float(speeds[hour]) if speeds and hour < len(speeds) else None
        deg  = float(dirs[hour])   if dirs   and hour < len(dirs)   else None

        # Weather string: temperature + condition
        w_parts: list[str] = [f'{temp:.0f}\u00b0C']
        if code is not None:
            desc = _WMO.get(code, '')
            if desc:
                w_parts.append(desc)
        weather_str = '  '.join(w_parts)

        # Wind string: compass direction + speed
        wind_str = ''
        if spd is not None:
            if deg is not None:
                wind_str = f'{_compass(deg)}  {spd:.0f} km/h'
            else:
                wind_str = f'{spd:.0f} km/h'

        return weather_str, wind_str
    except Exception:
        return '', ''


def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    except Exception as exc:
        logger.debug('Weather cache save failed: %s', exc)
