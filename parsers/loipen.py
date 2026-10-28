"""Fetches nearby Nordic ski trails (Loipen) via the Overpass API (OpenStreetMap)."""
import math

import requests

_OVERPASS_URLS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass.openstreetmap.ru/api/interpreter',
]
_HEADERS = {
    'User-Agent': 'holiday-house-comparison/1.0 (github.com/Gr3yh0und/holiday-house-comparison)',
}

# Circuit breaker: after this many consecutive all-endpoint failures, stop
# trying for the remainder of the process.
_FAILURE_THRESHOLD = 2


class _CircuitBreaker:
    """Counts consecutive all-endpoint network failures; trips at the threshold."""

    def __init__(self, threshold):
        self.threshold = threshold
        self.consecutive_failures = 0
        self.open = False

    def reset(self):
        self.consecutive_failures = 0
        self.open = False

    def record_success(self):
        self.consecutive_failures = 0

    def record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold:
            self.open = True


_breaker = _CircuitBreaker(_FAILURE_THRESHOLD)


def fetch(lat, lon, radius_m=10000):
    """Return a list of Nordic ski trails within radius_m metres of lat/lon.

    Returns None on network/server error so callers can distinguish a failed
    fetch from a location that genuinely has no trails (empty list).
    After _FAILURE_THRESHOLD consecutive failures across all endpoints the
    circuit breaker trips and all further calls return None immediately;
    _breaker.reset() re-arms it for a retry pass. Parse errors are data
    problems, not outages, and never count toward the breaker.
    """
    if _breaker.open:
        print('  [loipen] circuit breaker open — skipping Overpass query')
        return None

    query = (
        f'[out:json][timeout:30];'
        f'(way["piste:type"="nordic"](around:{radius_m},{lat},{lon});'
        f'relation["piste:type"="nordic"](around:{radius_m},{lat},{lon}););'
        f'out body geom;'
    )
    data = None
    for url in _OVERPASS_URLS:
        try:
            resp = requests.post(url, data={'data': query}, timeout=45, headers=_HEADERS)
            resp.raise_for_status()
            resp.encoding = 'utf-8'  # Overpass always returns UTF-8; prevent misdetection
            data = resp.json()
            break
        except Exception as e:  # pylint: disable=broad-except
            print(f'  [loipen] Overpass error ({url}): {e}')

    if data is None:
        _breaker.record_failure()
        if _breaker.open:
            print(
                f'  [loipen] {_breaker.consecutive_failures} consecutive failures — '
                'circuit breaker tripped, skipping remaining Overpass queries this run'
            )
        else:
            print(
                f'  [loipen] all endpoints failed ({_breaker.consecutive_failures}/{_FAILURE_THRESHOLD} '
                'before circuit breaker trips)'
            )
        return None

    _breaker.record_success()
    try:
        return _parse(data, lat, lon)
    except Exception as e:  # pylint: disable=broad-except
        print(f'  [loipen] could not parse Overpass response: {e}')
        return None


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
