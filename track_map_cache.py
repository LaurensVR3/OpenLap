"""
track_map_cache.py — Fetch and cache OpenStreetMap motor-racing circuit outlines
via the Overpass API.

Cached on disk in ~/.openlap/track_maps/ so Overpass is only queried once
per ~111 km grid cell, and refreshed after _CACHE_DAYS days.
"""
from __future__ import annotations

import json
import logging
import math
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_CACHE_DIR       = Path.home() / '.openlap' / 'track_maps'
_OVERPASS        = 'https://overpass-api.de/api/interpreter'
_SEARCH_RADIUS_M = 8000   # metres around GPS centroid
_CACHE_DAYS      = 30     # refresh candidates after this many days
_MAX_GEOM_PTS    = 500    # downsample polygon to at most this many points
_TIMEOUT_S       = 10


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f'{key}.json'


def _query_overpass(lat: float, lon: float, radius_m: int = _SEARCH_RADIUS_M) -> list:
    # Union of all common OSM tagging patterns for racing/karting circuits.
    # Surveyed real-world examples:
    #   Spa-Francorchamps : leisure=track,        sport=motor_racing
    #   Circuit Zolder    : leisure=sports_centre, sport=motor
    #   Karting Genk      : leisure=sports_centre, sport=karting
    #   Karting Amay      : leisure=track (no sport tag) / highway=raceway
    sport_re = 'motor_racing|karting|motorsport|motor'
    a = f'{lat:.6f},{lon:.6f}'
    query = (
        f'[out:json][timeout:30];'
        f'('
        f'  way(around:{radius_m},{a})["leisure"="track"];'
        f'  way(around:{radius_m},{a})["leisure"="sports_centre"]["sport"~"{sport_re}"];'
        f'  way(around:{radius_m},{a})["highway"="raceway"];'
        f');'
        f'out geom;'
    )
    data = urllib.parse.urlencode({'data': query}).encode()
    req  = urllib.request.Request(_OVERPASS, data=data, method='POST')
    req.add_header('User-Agent', 'OpenLap/1.0 (telemetry overlay)')
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode()).get('elements', [])


def _query_overpass_areas(lat: float, lon: float, radius_m: int = _SEARCH_RADIUS_M) -> list:
    """Query for track surface area polygons (closed ways + multipolygon relations)."""
    sport_re = 'motor_racing|karting|motorsport|motor'
    a = f'{lat:.6f},{lon:.6f}'
    query = (
        f'[out:json][timeout:30];'
        f'('
        # Explicit area-tagged track surfaces
        f'  way(around:{radius_m},{a})["area"="yes"]["leisure"="track"];'
        f'  way(around:{radius_m},{a})["area"="yes"]["highway"="raceway"];'
        # Sports-centre facility boundaries — inherently closed area polygons
        f'  way(around:{radius_m},{a})["leisure"="sports_centre"]["sport"~"{sport_re}"];'
        # Multipolygon relations for complex circuit shapes
        f'  relation(around:{radius_m},{a})["type"="multipolygon"]["leisure"="track"];'
        f'  relation(around:{radius_m},{a})["type"="multipolygon"]["highway"="raceway"];'
        f'  relation(around:{radius_m},{a})["type"="multipolygon"]["leisure"="sports_centre"]["sport"~"{sport_re}"];'
        f');'
        f'out geom;'
    )
    data = urllib.parse.urlencode({'data': query}).encode()
    req  = urllib.request.Request(_OVERPASS, data=data, method='POST')
    req.add_header('User-Agent', 'OpenLap/1.0 (telemetry overlay)')
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode()).get('elements', [])


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _centroid(lats: list, lons: list) -> tuple:
    n = max(len(lats), 1)
    return sum(lats) / n, sum(lons) / n


def _downsample(pts: list, max_pts: int = _MAX_GEOM_PTS) -> list:
    if len(pts) <= max_pts:
        return pts
    step = len(pts) / max_pts
    return [pts[int(i * step)] for i in range(max_pts)]


def _osm_label(tags: dict, osm_id: str) -> str:
    sport   = tags.get('sport', '').replace('_', ' ').title()
    leisure = tags.get('leisure', '')
    highway = tags.get('highway', '')
    if highway == 'raceway':
        kind = f'{sport} Raceway' if sport else 'Raceway'
    elif leisure == 'sports_centre':
        kind = f'{sport} Centre' if sport else 'Sports Centre'
    else:
        kind = f'{sport} Track' if sport else 'Track'
    return f'{kind} (way {osm_id})'


def _parse_area_elements(elements: list) -> List[dict]:
    """Extract [{lats, lons}] polygon data from Overpass area elements."""
    areas = []
    for el in elements:
        if el.get('type') == 'relation':
            # Multipolygon: collect outer member rings
            for member in el.get('members', []):
                if member.get('role') == 'outer':
                    geom = member.get('geometry', [])
                    if len(geom) >= 3:
                        pts = _downsample(geom)
                        areas.append({
                            'lats': [g['lat'] for g in pts],
                            'lons': [g['lon'] for g in pts],
                        })
        else:
            geom = el.get('geometry', [])
            if len(geom) >= 3:
                pts = _downsample(geom)
                areas.append({
                    'lats': [g['lat'] for g in pts],
                    'lons': [g['lon'] for g in pts],
                })
    return areas


def fetch_candidates(lat: float, lon: float) -> List[dict]:
    """Return [{osm_id, name, geometry:[{lat,lon}], centroid_dist_m}] for nearby tracks.

    Results are disk-cached per ≈1-degree grid cell (≈111 km).
    Also fetches and caches area polygons for visual rendering.
    Returns [] on network error.
    """
    grid_lat = round(lat, 1)
    grid_lon = round(lon, 1)
    key = f'candidates_{grid_lat:.1f}_{grid_lon:.1f}'
    cp  = _cache_path(key)

    if cp.exists():
        try:
            age_days = (time.time() - cp.stat().st_mtime) / 86400
            if age_days < _CACHE_DAYS:
                with open(cp, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                for c in cached:
                    geom = c.get('geometry', [])
                    if geom:
                        clat = sum(g['lat'] for g in geom) / len(geom)
                        clon = sum(g['lon'] for g in geom) / len(geom)
                        c['centroid_dist_m'] = _haversine_m(lat, lon, clat, clon)
                # Fetch area polygons if the cache entry predates area support
                areas_cp = _cache_path(f'areas_{grid_lat:.1f}_{grid_lon:.1f}')
                if not areas_cp.exists():
                    try:
                        area_elements = _query_overpass_areas(lat, lon)
                        areas = _parse_area_elements(area_elements)
                        with open(areas_cp, 'w', encoding='utf-8') as f:
                            json.dump(areas, f)
                    except Exception as exc:
                        logger.warning('Overpass areas (deferred) failed: %s', exc)
                return cached
        except Exception:
            pass

    try:
        elements = _query_overpass(lat, lon)
    except Exception as exc:
        logger.warning('Overpass query failed: %s', exc)
        return []

    candidates = []
    for el in elements:
        geom_raw = el.get('geometry', [])
        if not geom_raw:
            continue
        osm_id   = str(el.get('id', ''))
        tags     = el.get('tags', {})
        name     = (tags.get('name')
                    or tags.get('ref')
                    or tags.get('description')
                    or _osm_label(tags, osm_id))
        geometry = _downsample([{'lat': g['lat'], 'lon': g['lon']} for g in geom_raw])

        # Cache individual geometry for fast reload by osm_id
        way_cp = _cache_path(f'way_{osm_id}')
        if not way_cp.exists():
            try:
                with open(way_cp, 'w', encoding='utf-8') as f:
                    json.dump(geometry, f)
            except Exception:
                pass

        clat = sum(g['lat'] for g in geometry) / len(geometry)
        clon = sum(g['lon'] for g in geometry) / len(geometry)
        candidates.append({
            'osm_id':          osm_id,
            'name':            name,
            'geometry':        geometry,
            'centroid_dist_m': _haversine_m(lat, lon, clat, clon),
        })

    try:
        with open(cp, 'w', encoding='utf-8') as f:
            json.dump(candidates, f)
    except Exception:
        pass

    # Fetch and cache area polygons alongside candidates
    try:
        area_elements = _query_overpass_areas(lat, lon)
        areas = _parse_area_elements(area_elements)
        areas_cp = _cache_path(f'areas_{grid_lat:.1f}_{grid_lon:.1f}')
        with open(areas_cp, 'w', encoding='utf-8') as f:
            json.dump(areas, f)
    except Exception as exc:
        logger.warning('Overpass areas query failed: %s', exc)

    return candidates


def load_geometry(osm_id: str) -> List[dict]:
    """Load [{lat, lon}] for a cached way. Returns [] if not cached."""
    try:
        with open(_cache_path(f'way_{osm_id}'), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def load_areas(lat: float, lon: float) -> List[dict]:
    """Load cached area polygons [{lats, lons}] for a grid cell. Returns [] if not cached."""
    grid_lat = round(lat, 1)
    grid_lon = round(lon, 1)
    try:
        cp = _cache_path(f'areas_{grid_lat:.1f}_{grid_lon:.1f}')
        with open(cp, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def auto_select(candidates: List[dict], gps_lats: list, gps_lons: list) -> Optional[str]:
    """Return the osm_id of the best-matching candidate, or None.

    Picks the candidate whose geometry centroid is closest to the GPS trace
    centroid, but only accepts if within 3 km (avoids wrong-circuit match).
    """
    if not candidates or not gps_lats:
        return None
    gps_clat, gps_clon = _centroid(gps_lats, gps_lons)
    best_id   = None
    best_dist = float('inf')
    for c in candidates:
        geom = c.get('geometry', [])
        if not geom:
            continue
        clat = sum(g['lat'] for g in geom) / len(geom)
        clon = sum(g['lon'] for g in geom) / len(geom)
        dist = _haversine_m(gps_clat, gps_clon, clat, clon)
        if dist < best_dist:
            best_dist = dist
            best_id   = c['osm_id']
    return best_id if best_dist < 3000 else None
