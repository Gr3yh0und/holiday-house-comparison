import json
import re
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
    if 'rodelwelten.com' not in url:
        return dict(EMPTY, track=[])

    if not force_refresh and url in _cache:
        entry = _cache[url]
        age = datetime.now() - datetime.fromisoformat(entry['fetched_at'])
        if age < CACHE_TTL:
            print(f"  [cache] {url}")
            return entry['data']

    try:
        response = requests.get(url, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')

        name_el = soup.find('h1')
        route_name = name_el.text.strip() if name_el else 'N/A'

        # Build lookup dict from all <table class="table details"> th/td pairs
        facts = {}
        for table in soup.find_all('table', class_='details'):
            for row in table.find_all('tr'):
                th = row.find('th')
                td = row.find('td')
                if th and td:
                    facts[th.text.strip()] = td.text.strip()

        def get(key):
            return facts.get(key) or 'N/A'

        op_el = soup.find('div', class_='operator')
        operator = op_el.text.strip() if op_el else 'N/A'

        text = soup.get_text()
        hours_m = re.search(r'(\d{2}:\d{2})\s*Uhr\s*bis\s*(\d{2}:\d{2})\s*Uhr', text)
        opening_hours = f"{hours_m.group(1)} - {hours_m.group(2)}" if hours_m else 'N/A'

        diff_m = re.search(r'als\s+(leichte|mittelschwierige|schwierige)', text)
        difficulty = diff_m.group(1) if diff_m else 'N/A'

        slope_key = next((k for k in facts if k.startswith('Gef')), None)
        slope = ('Ø ' + facts[slope_key]) if slope_key else 'N/A'

        track = _extract_track(response.text)
        huts = _extract_huts(response.text)

        result = {
            'name':             route_name,
            'length':           get('Länge'),
            'night_sleighing':  get('Beleuchtung'),
            'public_transport': get('Öffentliche Anreise'),
            'walking_time':     get('Gehzeit'),
            'sled_rental':      get('Rodelverleih'),
            'avalanche_danger': get('Lawinengefahr'),
            'height_top':       get('Höhe oben'),
            'height_bottom':    get('Höhe unten'),
            'elevation_diff':   get('Höhenmeter'),
            'slope':            slope,
            'separate_ascent':  get('Aufstieg getrennt'),
            'ascent_aid':       get('Aufstiegshilfe'),
            'difficulty':       difficulty,
            'operator':         operator,
            'opening_hours':    opening_hours,
            'track':            track,
            'huts':             huts,
        }
        _cache[url] = {'fetched_at': datetime.now().isoformat(), 'data': result}
        return result

    except Exception as e:
        print(f"  [rodelwelten] error scraping {url}: {e}")
        return {k: 'Error' for k in EMPTY}


def _extract_track(page_text):
    # Case 1: external GPX file reference
    gpx_m = re.search(r"data\s*=\s*'(/fileadmin/user_upload/gpx/[^']+)'", page_text)
    if gpx_m:
        try:
            import xml.etree.ElementTree as ET
            gpx_resp = requests.get('https://www.rodelwelten.com' + gpx_m.group(1), timeout=10)
            root = ET.fromstring(gpx_resp.content)
            ns = {'g': 'http://www.topografix.com/GPX/1/1'}
            pts = [[float(p.get('lat')), float(p.get('lon'))]
                   for p in root.findall('.//g:trkpt', ns)]
            if pts:
                step = max(1, len(pts) // 60)
                return pts[::step]
        except Exception as e:
            print(f"  [gpx] failed to fetch track: {e}")

    # Case 2: inline JSON coordinates (may have multiple sledrun segments)
    sledrun_matches = re.findall(
        r"JSON\.parse\('(?:\\)?\[([^']+)\]'\);\s*paths\.push\(\{\s*type\s*:\s*'sledrun'",
        page_text, re.DOTALL)
    if sledrun_matches:
        try:
            pts = []
            for m in sledrun_matches:
                pts += [[p['lat'], p['lng']] for p in json.loads('[' + m + ']')]
            if pts:
                step = max(1, len(pts) // 60)
                return pts[::step]
        except Exception as e:
            print(f"  [track] failed to parse inline track: {e}")

    return []


def _extract_huts(page_text):
    soup = BeautifulSoup(page_text, 'html.parser')
    huts = []
    for div in soup.find_all('div', class_='hut-content'):
        h4 = div.find('h4')
        if not h4:
            continue
        name = h4.get_text(strip=True)
        url = None
        for row in div.find_all('tr'):
            th = row.find('th')
            td = row.find('td')
            if th and td and th.get_text(strip=True).rstrip(':') == 'Web':
                a = td.find('a', href=True)
                if a:
                    url = a['href']
                break
        huts.append({'name': name, 'url': url})
    return huts
