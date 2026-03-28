import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

CACHE_TTL = timedelta(days=1)

_cache: dict = {}
_cache_file: str = ''

EMPTY = {k: 'N/A' for k in [
    'name', 'length', 'night_sleighing', 'public_transport', 'walking_time', 'sled_rental',
    'avalanche_danger', 'height_top', 'height_bottom', 'elevation_diff', 'slope',
    'separate_ascent', 'ascent_aid', 'difficulty', 'operator', 'opening_hours',
]}
EMPTY['track'] = []
EMPTY['huts'] = []


def load_cache(path):
    global _cache, _cache_file
    _cache_file = path
    import os
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            _cache = json.load(f)


def save_cache():
    import os
    os.makedirs(os.path.dirname(_cache_file), exist_ok=True)
    with open(_cache_file, 'w', encoding='utf-8') as f:
        json.dump(_cache, f, ensure_ascii=False, indent=2)


def scrape(url, force_refresh=False):
    if 'outdooractive.com' not in url:
        return dict(EMPTY)

    # Normalise URL: strip fragment so cache key is stable
    cache_key = url.split('#')[0].rstrip('/')

    if not force_refresh and cache_key in _cache:
        entry = _cache[cache_key]
        age = datetime.now() - datetime.fromisoformat(entry['fetched_at'])
        if age < CACHE_TTL:
            print(f"  [cache] {cache_key}")
            return entry['data']

    route_id = _extract_id(url)
    if not route_id:
        print(f"  [outdooractive] could not extract route ID from {url}")
        return dict(EMPTY)

    try:
        response = requests.get(cache_key, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')

        ld = _parse_json_ld(soup)
        amenities = {a['name']: a.get('value') for a in ld.get('amenityFeature', [])}

        name = ld.get('name', 'N/A')

        # Length
        dist_obj = ld.get('potentialAction', {}).get('distance', '')
        if isinstance(dist_obj, dict):
            dist_val = dist_obj.get('value')
        elif isinstance(dist_obj, (int, float)):
            dist_val = float(dist_obj)
        else:
            m = re.search(r'\d[\d.]*', str(dist_obj))
            dist_val = float(m.group()) if m else None
        length = f"{dist_val / 1000:.1f} km" if dist_val is not None else 'N/A'

        # Altitudes
        alt_top = amenities.get('altitude_to')
        alt_bottom = amenities.get('altitude_from')
        height_top = f"{int(alt_top)} m" if alt_top is not None else 'N/A'
        height_bottom = f"{int(alt_bottom)} m" if alt_bottom is not None else 'N/A'

        if alt_top is not None and alt_bottom is not None:
            elevation_diff = f"{int(abs(alt_top - alt_bottom))} m"
        else:
            elevation_diff = 'N/A'

        # Slope
        slope = 'N/A'
        if dist_val and dist_val > 0 and alt_top is not None and alt_bottom is not None:
            pct = abs(alt_top - alt_bottom) / dist_val * 100
            slope = f"Ø {pct:.0f} %"

        # Difficulty
        difficulty_map = {'easy': 'leicht', 'moderate': 'mittelschwierig', 'difficult': 'schwierig'}
        difficulty = difficulty_map.get(amenities.get('difficulty', ''), amenities.get('difficulty', 'N/A'))

        # Ascent aid
        ascent_aid = 'Ja' if amenities.get('Bergbahnauf-/-abstieg') else 'N/A'

        # Operator
        publisher = ld.get('publisher', {})
        operator = publisher.get('name', 'N/A') if isinstance(publisher, dict) else 'N/A'

        # Page text for fields not in structured data
        page_text = soup.get_text(' ', strip=True)

        night_sleighing = 'Ja' if re.search(r'Nachtrodeln|Nachtschlitteln|Abendfahrt', page_text) else 'N/A'
        public_transport = (
            'Ja' if re.search(r'öffentlich|Bahn und Bus|ÖV|Postauto', page_text, re.IGNORECASE) else 'N/A'
        )
        sled_rental = (
            'Ja' if re.search(r'Schlittenmiete|Schlittenverleih|Rodelverleih|mieten', page_text, re.IGNORECASE)
            else 'N/A'
        )

        hours_m = re.search(r'(\d{1,2}[:.]\d{2})\s*(?:Uhr)?\s*[-–]\s*(\d{1,2}[:.]\d{2})\s*Uhr', page_text)
        opening_hours = f"{hours_m.group(1)} - {hours_m.group(2)}" if hours_m else 'N/A'

        track = _extract_track(route_id)

        result = {
            'name':             name,
            'length':           length,
            'night_sleighing':  night_sleighing,
            'public_transport': public_transport,
            'walking_time':     'N/A',
            'sled_rental':      sled_rental,
            'avalanche_danger': 'N/A',
            'height_top':       height_top,
            'height_bottom':    height_bottom,
            'elevation_diff':   elevation_diff,
            'slope':            slope,
            'separate_ascent':  'N/A',
            'ascent_aid':       ascent_aid,
            'difficulty':       difficulty,
            'operator':         operator,
            'opening_hours':    opening_hours,
            'track':            track,
            'huts':             [],
        }
        _cache[cache_key] = {'fetched_at': datetime.now().isoformat(), 'data': result}
        return result

    except Exception as e:
        print(f"  [outdooractive] error scraping {url}: {e}")
        return dict(EMPTY)


def _extract_id(url):
    m = re.search(r'/(\d{6,})', url)
    return m.group(1) if m else None


def _parse_json_ld(soup):
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
            if isinstance(data, list):
                data = data[0]
            if 'amenityFeature' in data or 'potentialAction' in data:
                return data
        except (json.JSONDecodeError, TypeError):
            continue
    return {}


def _debug(url):
    """Run the parser on a single URL and print results, for debugging."""
    print(f"Scraping: {url}\n")
    result = scrape(url, force_refresh=True)
    track = result.pop('track', [])
    huts = result.pop('huts', [])
    for k, v in result.items():
        print(f"  {k:<20} {v}")
    print(f"  {'track':<20} {len(track)} points")
    if huts:
        print(f"  {'huts':<20} {huts}")
    if not track:
        print("\n  [!] No track extracted — checking raw page for clues...")
        cache_key = url.split('#')[0].rstrip('/')
        route_id = _extract_id(url)
        print(f"  Route ID: {route_id}")
        resp = requests.get(cache_key, timeout=15)
        soup = BeautifulSoup(resp.content, 'html.parser')
        ld_tags = soup.find_all('script', type='application/ld+json')
        print(f"  JSON-LD blocks found: {len(ld_tags)}")
        for i, tag in enumerate(ld_tags):
            try:
                data = json.loads(tag.string or '')
                keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
                print(f"    [{i}] keys: {keys}")
                if isinstance(data, dict):
                    pa = data.get('potentialAction', 'MISSING')
                    print(f"    [{i}] potentialAction = {json.dumps(pa, ensure_ascii=False, indent=6)}")
                    af = data.get('amenityFeature', 'MISSING')
                    print(f"    [{i}] amenityFeature = {json.dumps(af, ensure_ascii=False, indent=6)}")
            except Exception as e:
                print(f"    [{i}] parse error: {e}")
        gpx_url = f"https://www.outdooractive.com/de/download.tour.gpx?i={route_id}"
        print(f"  GPX URL: {gpx_url}")
        try:
            gpx_resp = requests.get(gpx_url, timeout=15)
            print(f"  GPX status: {gpx_resp.status_code}, content length: {len(gpx_resp.content)}")
            print(f"  GPX preview: {gpx_resp.text[:300]}")
        except Exception as e:
            print(f"  GPX fetch failed: {e}")


def _extract_track(route_id):
    gpx_url = f"https://www.outdooractive.com/de/download.tour.gpx?i={route_id}"
    try:
        resp = requests.get(gpx_url, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {'g': 'http://www.topografix.com/GPX/1/1'}
        pts = [[float(p.get('lat')), float(p.get('lon'))]
               for p in root.findall('.//g:trkpt', ns)]
        if pts:
            step = max(1, len(pts) // 60)
            return pts[::step]
    except Exception as e:
        print(f"  [outdooractive] failed to fetch GPX track: {e}")
    return []


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m parsers.outdooractive <url>")
        sys.exit(1)
    _debug(sys.argv[1])
