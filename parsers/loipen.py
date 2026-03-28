"""Fetches nearby Nordic ski trails (Loipen) via the Overpass API (OpenStreetMap)."""
import math

import requests

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
_HEADERS = {
    'User-Agent': 'holiday-house-comparison/1.0 (github.com/Gr3yh0und/holiday-house-comparison)',
}


def fetch(lat, lon, radius_m=10000):
    """Return a list of Nordic ski trails within radius_m metres of lat/lon."""
    query = (
        f'[out:json][timeout:30];'
        f'(way["piste:type"="nordic"](around:{radius_m},{lat},{lon});'
        f'relation["piste:type"="nordic"](around:{radius_m},{lat},{lon}););'
        f'out body geom;'
    )
    try:
        resp = requests.post(OVERPASS_URL, data={'data': query}, timeout=45, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # pylint: disable=broad-except
        print(f'  [loipen] Overpass error: {e}')
        return []
    return _parse(data, lat, lon)


def _parse(data, house_lat, house_lon):
    seen = {}  # name → loipe; prefer relations over ways
    for el in data.get('elements', []):
        if el['type'] == 'way':
            loipe = _parse_way(el, house_lat, house_lon)
        elif el['type'] == 'relation':
            loipe = _parse_relation(el, house_lat, house_lon)
        else:
            continue
        if loipe is None:
            continue
        name = loipe['name']
        if name not in seen or el['type'] == 'relation':
            seen[name] = loipe
    return sorted(seen.values(), key=lambda x: x['distance_km'])


def _parse_way(el, house_lat, house_lon):
    tags = el.get('tags', {})
    geom = el.get('geometry', [])
    if not geom:
        return None
    track = [[p['lat'], p['lon']] for p in geom]
    return {
        'name': tags.get('name') or tags.get('ref') or 'Loipe',
        'difficulty': tags.get('piste:difficulty', ''),
        'grooming': tags.get('piste:grooming', ''),
        'length_km': round(_calc_length_km(track), 1),
        'distance_km': round(_nearest_km(track, house_lat, house_lon), 1),
        'track': _downsample(track, 150),
    }


def _parse_relation(el, house_lat, house_lon):
    tags = el.get('tags', {})
    track = []
    for member in el.get('members', []):
        if member.get('type') == 'way':
            for p in member.get('geometry', []):
                track.append([p['lat'], p['lon']])
    if not track:
        return None
    return {
        'name': tags.get('name') or tags.get('ref') or 'Loipe',
        'difficulty': tags.get('piste:difficulty', ''),
        'grooming': tags.get('piste:grooming', ''),
        'length_km': round(_calc_length_km(track), 1),
        'distance_km': round(_nearest_km(track, house_lat, house_lon), 1),
        'track': _downsample(track, 150),
    }


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def _calc_length_km(track):
    return sum(
        _haversine_km(track[i - 1][0], track[i - 1][1], track[i][0], track[i][1])
        for i in range(1, len(track))
    )


def _nearest_km(track, lat, lon):
    return min(_haversine_km(p[0], p[1], lat, lon) for p in track)


def _downsample(track, max_points):
    if len(track) <= max_points:
        return track
    step = len(track) / max_points
    return [track[int(i * step)] for i in range(max_points)]
