import argparse
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from flask import Flask, render_template

from parsers import booking, fewo, huetten, interhome, rodelwelten, outdooractive, loipen as loipen_parser
from parsers.common import EMPTY as _PARSER_EMPTY

app = Flask(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'sled_runs.json')
CACHE_FILE_OA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'outdooractive.json')
LOIPEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'loipen.json')
# Scraped house data -- the hand-off between the scrape step and the render step.
# Replaced only by a scrape that passes _validate_scrape_output(); a run in
# progress writes HOUSES_PARTIAL_FILE instead, so a stopped or failed scrape
# never touches the data the next deploy renders from.
HOUSES_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'houses.json')
HOUSES_PARTIAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'houses.partial.json')
TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'translations')

# Bundled Chrome for Testing + matching chromedriver under webdriver/ (see README).
_WEBDRIVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webdriver')
if os.name == 'nt':
    CHROMEDRIVER_PATH = os.path.join(_WEBDRIVER_DIR, 'chromedriver-win64', 'chromedriver.exe')
    CHROME_BINARY_PATH = os.path.join(_WEBDRIVER_DIR, 'chrome-win64', 'chrome.exe')
else:
    CHROMEDRIVER_PATH = os.path.join(_WEBDRIVER_DIR, 'chromedriver-linux64', 'chromedriver')
    CHROME_BINARY_PATH = os.path.join(_WEBDRIVER_DIR, 'chrome-linux64', 'chrome')
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'VERSION')
HEALTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'public', 'health.json')
HOMELAB_ENV = os.environ.get('HOMELAB_ENV', '/etc/homelab/holiday-house-comparison.env')
CARTO_TILE_URL = 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png'

_DEFAULTS = {
    'loipen_radius_m': 10000,
    'loipen_cache_ttl_h': 24,
    'house_cache_ttl_h': 24,
    'fewo_cooldown_s': [20, 45],
}


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f:
            return {**_DEFAULTS, **json.load(f)}
    except FileNotFoundError:
        return dict(_DEFAULTS)


_config = load_config()


def load_translations(lang='de') -> dict:
    path = os.path.join(TRANSLATIONS_DIR, f'{lang}.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_all_translations() -> dict:
    result = {}
    for fname in os.listdir(TRANSLATIONS_DIR):
        if fname.endswith('.json'):
            lang_code = fname[:-5]
            with open(os.path.join(TRANSLATIONS_DIR, fname), encoding='utf-8') as f:
                result[lang_code] = json.load(f)
    return result


_translations = load_translations('bar-DE')
_all_translations = load_all_translations()
_lang = 'bar-DE'


def get_version():
    """Return the latest GitHub release tag, falling back to the latest git tag, then 'dev'."""
    try:
        resp = requests.get(
            'https://api.github.com/repos/Gr3yh0und/holiday-house-comparison/releases/latest',
            headers={'Accept': 'application/vnd.github+json'},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get('tag_name', '')
    except Exception:
        pass
    try:
        import subprocess
        tag = subprocess.check_output(
            ['git', 'describe', '--tags', '--abbrev=0'],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if tag:
            return tag
    except Exception:
        pass
    return 'dev'


def _parse_price(price):
    """Return price as float, or None if unparseable."""
    try:
        p = price.strip().lstrip('€').rstrip('€').strip()
        p = p.replace('\xa0', '').replace('\u202f', '').replace(' ', '')
        p = p.replace('.', '').replace(',', '.')
        return float(p)
    except (ValueError, AttributeError, TypeError):
        return None


_COUNTRY_FLAGS = {
    'Österreich': '🇦🇹',
    'Deutschland': '🇩🇪',
    'Schweiz': '🇨🇭',
    'Italien': '🇮🇹',
    'Frankreich': '🇫🇷',
}


_BROKER_NAMES = {
    'fewo-direkt.de': 'fewo-direkt',
    'booking.com': 'Booking.com',
    'huetten.com': 'huetten.com',
}


@app.template_filter('broker_name')
def broker_name(url):
    for domain, name in _BROKER_NAMES.items():
        if domain in url:
            return name
    return ''


@app.template_filter('price_inflate')
def price_inflate(price, pct):
    """Increase a price string by pct percent, return rounded to full euros."""
    val = _parse_price(price)
    if val is None:
        return price
    return f"{round(val * (1 + pct / 100))} €"


@app.template_filter('country_flag')
def country_flag(address):
    """Return the flag emoji for the country at the end of an 'City, Country' address."""
    if not address or address in ('N/A', 'Error'):
        return ''
    country = address.rsplit(',', 1)[-1].strip()
    if country.startswith('Canton of '):
        return _COUNTRY_FLAGS.get('Schweiz', '')
    return _COUNTRY_FLAGS.get(country, '')


@app.template_filter('normalize_price')
def normalize_price(price):
    if price in (None, 'N/A', 'Error'):
        return price
    val = _parse_price(price)
    return f"{int(val)} €" if val is not None else price


@app.template_filter('price_per_person')
def price_per_person(price, persons):
    try:
        price_val = _parse_price(price)
        if price_val is None:
            return None
        persons_val = int(persons)
        if persons_val == 0:
            return None
        return f"{round(price_val / persons_val)} €"
    except (ValueError, AttributeError, TypeError):
        return None


@app.template_filter('dedate')
def dedate(value):
    try:
        d = datetime.strptime(value, '%Y-%m-%d')
        weekdays = _translations.get('weekdays', [])
        day_name = weekdays[d.weekday()] if weekdays else ''
        return f"{d.strftime('%d.%m.%Y')} ({day_name})" if day_name else d.strftime('%d.%m.%Y')
    except (ValueError, TypeError):
        return value


def _fetch_loipen(lat, lon, radius_m=None, force_refresh=False):
    """Return nearby Nordic ski trails, cached per coordinate."""
    if radius_m is None:
        radius_m = _config['loipen_radius_m']
    ttl_s = _config['loipen_cache_ttl_h'] * 3600
    cache_key = f'{lat:.4f},{lon:.4f}'
    def _read_cache():
        try:
            with open(LOIPEN_CACHE_FILE, encoding='utf-8') as f:
                return json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            print(f'  [loipen] cache corrupted ({e}), discarding')
            return {}

    if not force_refresh and os.path.exists(LOIPEN_CACHE_FILE):
        cache = _read_cache()
        entry = cache.get(cache_key)
        if entry:
            age = (datetime.now() - datetime.strptime(
                entry['fetched_at'], '%Y-%m-%d %H:%M'
            )).total_seconds()
            if age < ttl_s:
                print(f'  [loipen] cache hit for {cache_key} ({len(entry["loipen"])} trails)')
                return entry['loipen']
    print(f'  [loipen] fetching Overpass for {cache_key} (radius {radius_m} m) ...')
    trails = loipen_parser.fetch(lat, lon, radius_m=radius_m)
    if trails is None:
        print('  [loipen] fetch failed — skipping cache write, returning []')
        return []
    print(f'  [loipen] found {len(trails)} trails')
    cache = _read_cache() if os.path.exists(LOIPEN_CACHE_FILE) else {}
    cache[cache_key] = {
        'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'loipen': trails,
    }
    with open(LOIPEN_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    return trails


def _make_driver():
    import undetected_chromedriver as uc

    options = uc.ChromeOptions()
    if os.path.exists(CHROME_BINARY_PATH):
        options.binary_location = CHROME_BINARY_PATH
        print(f'  [driver] using bundled Chrome: {CHROME_BINARY_PATH}')
    else:
        print('  [driver] bundled Chrome not found, falling back to system Chrome')
    # --no-sandbox and --disable-dev-shm-usage are known automation signals on
    # Windows and are unnecessary there; only add them on Linux (CI / Docker).
    if os.name != 'nt':
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
    # Randomise window size slightly so every session looks different
    w = random.randint(1280, 1920)
    h = random.randint(900, 1080)
    options.add_argument(f'--window-size={w},{h}')
    options.add_argument('--window-position=0,0')
    driver_path = CHROMEDRIVER_PATH if os.path.exists(CHROMEDRIVER_PATH) else None
    return uc.Chrome(options=options, driver_executable_path=driver_path)


BROKER_DOMAINS = {
    'fewo': 'fewo-direkt.de', 'booking': 'booking.com',
    'huetten': 'huetten.com', 'interhome': 'interhome.',
}


def scrape_house(url, driver=None):
    if 'fewo-direkt.de' in url:
        return fewo.scrape(url, driver)
    if 'booking.com' in url:
        return booking.scrape(url, driver)
    if 'huetten.com' in url:
        return huetten.scrape(url, driver)
    if 'interhome.' in url:
        return interhome.scrape(url, driver)
    return {k: 'N/A' for k in ['location', 'address', 'rooms', 'sqm', 'bathrooms',
                                 'room_config', 'price', 'time', 'train_station',
                                 'bus_stop', 'supermarket', 'rating', 'persons', 'sauna']}


def inject_dates(url, checkin, checkout):
    """Replace known date parameters in a URL with the given checkin/checkout dates.

    For fewo-direkt.de URLs the query string is stripped down to only the
    essential parameters (chkin, chkout, adults) to avoid sending referrer
    tokens, session IDs, and search context that DataDome may use for
    fingerprinting returning scrapers.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    if 'fewo-direkt.de' in parsed.netloc:
        adults = params.get('adults', ['8'])[0]
        clean_params = {'chkin': checkin, 'chkout': checkout, 'adults': adults}
        return urlunparse(parsed._replace(query=urlencode(clean_params)))

    date_map = {
        'chkin': checkin, 'chkout': checkout,
        'd1': checkin, 'd2': checkout,
        'startDate': checkin, 'endDate': checkout,
        'checkin': checkin, 'checkout': checkout,
        'arrival': checkin,  # interhome
    }
    for key, val in date_map.items():
        if key in params:
            params[key] = [val]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def _carto_api_key():
    """CARTO basemap key: CARTO_API_KEY from the environment, else from the
    homelab env file (WEBAPP_PROJECT_STANDARD.md §4). It ends up in the
    rendered page (the browser sends it with every tile request), but never
    in this repo -- the repo is public."""
    key = os.environ.get('CARTO_API_KEY', '').strip()
    if key:
        return key
    try:
        with open(HOMELAB_ENV, encoding='utf-8') as f:
            for line in f:
                name, sep, value = line.strip().partition('=')
                if sep and name.strip() == 'CARTO_API_KEY':
                    return value.strip().strip('"\'')
    except OSError:
        pass
    return ''


def _tile_url():
    key = _carto_api_key()
    if not key:
        print('  [map] no CARTO_API_KEY set -- map tiles will show "API KEY REQUIRED"')
        return CARTO_TILE_URL
    return f"{CARTO_TILE_URL}?{urlencode({'key': key})}"


def _render_html(title, trips, updated_at, version):
    return render_template(
        'index.html',
        t=_translations, all_translations=_all_translations, lang=_lang,
        title=title, trips=trips, updated_at=updated_at, version=version,
        tile_url=_tile_url(),
    )


def _write_json_atomic(path, payload):
    """Write JSON via a temp file + os.replace, so a reader (or a crash mid-write)
    never sees a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f'{path}.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _load_houses_cache():
    """Return cache/houses.json ({updated_at, status, trips}) or None."""
    try:
        with open(HOUSES_CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _load_cached_house(name, checkin, checkout):
    """Return a previously scraped house from cache/houses.json if it exists and is < 24 h old."""
    cached = _load_houses_cache()
    if not cached:
        return None
    try:
        updated_at = datetime.strptime(cached.get('updated_at', ''), '%Y-%m-%d %H:%M')
        if (datetime.now() - updated_at).total_seconds() > _config['house_cache_ttl_h'] * 3600:
            return None
        for trip in cached.get('trips', []):
            for h in trip.get('houses', []):
                if h.get('name') == name and h.get('checkin') == checkin and h.get('checkout') == checkout:
                    return h
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    return None


_image_checks = {}


def _image_ok(url):
    """True if url answers 200 with an image (checked once per run)."""
    if url not in _image_checks:
        try:
            resp = requests.get(url, timeout=15, stream=True,
                                headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'image/*'})
            _image_checks[url] = (resp.status_code == 200
                                  and resp.headers.get('content-type', '').startswith('image/'))
            resp.close()
        except requests.RequestException:
            _image_checks[url] = False
    return _image_checks[url]


def _scrape_one_house(house, trip_checkin, trip_checkout, driver=None, force_refresh=False, stats=None):
    raw_url = house.get('house_url', '')
    house_url = (
        inject_dates(raw_url, trip_checkin, trip_checkout)
        if raw_url and trip_checkin and trip_checkout
        else raw_url
    )
    if not house_url:
        print(f"No house_url for: {house['name']} — using input.json data only")
        house_info = dict(_PARSER_EMPTY, room_config=[], time='check_manually')
    else:
        print(f"Scraping house: {house['name']} ({house_url})")
        if stats is not None:
            stats['attempted'] += 1
        house_info = scrape_house(house_url, driver=driver)
        if house_info is not None and all(
            house_info.get(k) in (None, 'N/A', 'Error') for k in ('location', 'address', 'rooms', 'price')
        ):
            # A parser that crashed ('Error' everywhere) or found nothing at all
            # (blocked page, changed layout) is a failure, not a booked-out house.
            print("  -> scrape returned no data")
            house_info = None
        if house_info is None:
            cached = _load_cached_house(house['name'], trip_checkin, trip_checkout)
            if cached:
                print("  -> bot/scrape failure, using cached data from cache/houses.json")
                return cached
            print("  -> bot/scrape failure, no usable cache — returning empty result")
            if stats is not None:
                stats['failed'] += 1
            house_info = dict(_PARSER_EMPTY, room_config=[])
    house_info['name'] = house['name']
    house_info['house_url'] = house_url
    if house_info.get('time') == 'Unavailable' and house.get('price') and not house.get('time'):
        print(f"  -> booked out, but input.json sets price {house['price']!r}: "
              "the page shows that old price -- remove it from input.json")
    for field in ('address', 'rooms', 'persons', 'sqm', 'bathrooms',
                  'room_config', 'price', 'time', 'rating',
                  'supermarket', 'train_station', 'bus_stop', 'sauna', 'nearest_sled_run', 'notes'):
        if house.get(field):
            house_info[field] = house[field]
    # Prefer the hand-picked photo from input.json, but listing photos get deleted
    # (booking.com 404s them) -- then fall back to the photo the parser scraped.
    scraped_image = house_info.pop('image_url', None)
    if not (isinstance(scraped_image, str) and scraped_image.startswith(('https://', 'http://'))):
        scraped_image = None  # scraped content is untrusted: only plain web URLs into <img src>
    if house.get('image_url') and (not scraped_image or _image_ok(house['image_url'])):
        house_info['image_url'] = house['image_url']
    elif scraped_image:
        if house.get('image_url'):
            print(f"  -> image_url in input.json is dead, using the listing photo: {scraped_image}")
        house_info['image_url'] = scraped_image
    if 'lat' in house and 'lon' in house:
        house_info['lat'] = house['lat']
        house_info['lon'] = house['lon']
    if 'direct_url' in house:
        house_info['direct_url'] = house['direct_url']
    if 'pois' in house:
        house_info['pois'] = house['pois']
        # Auto-compute bus_stop distance string from a 'bus' POI if not already set
        if not house_info.get('bus_stop') and house.get('lat') and house.get('lon'):
            import math as _math
            def _hdist(la1, lo1, la2, lo2):
                earth_r = 6371
                dla = _math.radians(la2 - la1)
                dlo = _math.radians(lo2 - lo1)
                a = (_math.sin(dla / 2) ** 2
                     + _math.cos(_math.radians(la1)) * _math.cos(_math.radians(la2))
                     * _math.sin(dlo / 2) ** 2)
                return earth_r * 2 * _math.asin(_math.sqrt(a))
            for poi in house['pois']:
                if poi.get('type') == 'bus' and poi.get('lat') and poi.get('lon'):
                    d = _hdist(house['lat'], house['lon'], poi['lat'], poi['lon'])
                    mins = round(d / 5 * 60) if d < 1.5 else round(d / 40 * 60)
                    house_info['bus_stop'] = f"{poi['label']} ({d:.1f}".replace('.', ',') + f" km · {mins} min)"
                    break
    if 'train_track' in house:
        house_info['train_track'] = house['train_track']
    if 'bus_track' in house:
        house_info['bus_track'] = house['bus_track']
    house_info['checkin'] = trip_checkin
    house_info['checkout'] = trip_checkout
    house_info['sled_runs'] = []
    for sled_run_url in house.get('sled_run_urls', []):
        if 'outdooractive.com' in sled_run_url:
            sled_run_info = outdooractive.scrape(sled_run_url, force_refresh=force_refresh)
        else:
            sled_run_info = rodelwelten.scrape(sled_run_url, force_refresh=force_refresh)
        sled_run_info['url'] = sled_run_url
        if not sled_run_info.get('name') or sled_run_info['name'] == 'N/A':
            sled_run_info['name'] = sled_run_url.rstrip('/').split('/')[-1].replace('-', ' ').title()
        house_info['sled_runs'].append(sled_run_info)
    def _length_m(length):
        m = re.search(r'([\d.,]+)\s*(km|m)\b', length, re.IGNORECASE)
        if not m:
            return 0
        val = float(m.group(1).replace(',', '.'))
        return val * 1000 if m.group(2).lower() == 'km' else val
    house_info['sled_runs'].sort(key=lambda r: _length_m(r['length']), reverse=True)
    # Overpass auto-discovery (skipped when disable_loipen is true)
    if house.get('disable_loipen'):
        house_info['loipen'] = []
    elif house.get('lat') and house.get('lon'):
        house_info['loipen'] = _fetch_loipen(
            house['lat'], house['lon'],
            radius_m=house.get('loipen_radius_m'),
            force_refresh=force_refresh,
        )
    else:
        house_info['loipen'] = []

    # Manual loipen_urls — always parsed regardless of disable_loipen
    for loipen_url in house.get('loipen_urls', []):
        if 'outdooractive.com' in loipen_url:
            info = outdooractive.scrape(loipen_url, force_refresh=force_refresh)
        else:
            info = rodelwelten.scrape(loipen_url, force_refresh=force_refresh)
        track = info.get('track', [])
        length_str = info.get('length', '0')
        length_m = re.search(r'([\d.,]+)\s*(km|m)\b', length_str, re.I)
        if length_m:
            length_km = float(length_m.group(1).replace(',', '.'))
            if length_m.group(2).lower() == 'm':
                length_km /= 1000
        else:
            length_km = 0.0
        import math as _math
        def _hav(la1, lo1, la2, lo2):
            earth_r = 6371.0
            dla = _math.radians(la2 - la1)
            dlo = _math.radians(lo2 - lo1)
            a = (_math.sin(dla / 2) ** 2
                 + _math.cos(_math.radians(la1)) * _math.cos(_math.radians(la2))
                 * _math.sin(dlo / 2) ** 2)
            return earth_r * 2 * _math.asin(_math.sqrt(a))
        if track and house.get('lat') and house.get('lon'):
            distance_km = round(min(_hav(p[0], p[1], house['lat'], house['lon']) for p in track), 1)
        else:
            distance_km = 0.0
        house_info['loipen'].append({
            'name': info.get('name') or loipen_url.rstrip('/').split('/')[-1].replace('-', ' ').title(),
            'difficulty': info.get('difficulty', ''),
            'grooming': '',
            'length_km': round(length_km, 1),
            'distance_km': distance_km,
            'track': track,
        })
    # Re-sort combined list by distance
    house_info['loipen'].sort(key=lambda x: x['distance_km'])
    return house_info


def _normalize_input(data):
    """Convert house-centric input format to trip-centric format if needed.

    House-centric format has a top-level 'houses' list where each house
    contains a 'trips' array with per-trip name/dates (and optional overrides).
    This is converted to the canonical trip-centric format used throughout the app.
    """
    if 'trips' in data:
        return data
    trips = {}
    trip_order = []
    for house in data.get('houses', []):
        if house.get('template'):
            continue
        house_base = {k: v for k, v in house.items() if k != 'trips'}
        for trip_entry in house.get('trips', []):
            name = trip_entry['name']
            if name not in trips:
                trips[name] = {
                    'name': name,
                    'checkin': trip_entry.get('checkin', ''),
                    'checkout': trip_entry.get('checkout', ''),
                    'houses': [],
                }
                trip_order.append(name)
            merged = dict(house_base)
            for key, val in trip_entry.items():
                if key not in ('name', 'checkin', 'checkout'):
                    merged[key] = val
            trips[name]['houses'].append(merged)
    return {'title': data.get('title', ''), 'trips': [trips[n] for n in trip_order]}


def _read_repo_version():
    """Return this repo's VERSION file content, or 'dev' if missing."""
    try:
        with open(VERSION_FILE, encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return 'dev'


def _iso_utc(updated_at):
    """'2026-10-06 12:44' (cache/houses.json, server local time) -> '2026-10-06T10:44:00Z'.
    §6a wants ISO 8601 UTC in health.json; the page itself keeps showing local time."""
    if not updated_at:
        return updated_at
    try:
        local = datetime.strptime(updated_at, '%Y-%m-%d %H:%M').astimezone()
    except ValueError:
        return updated_at
    return local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _write_health(status, last_update):
    """Write public/health.json (WEBAPP_PROJECT_STANDARD.md §6a).

    last_update is the time of the last successful scrape (cache/houses.json's
    updated_at), not this process's runtime -- rendering old data doesn't make
    it fresh, and a failed scrape (status='down') didn't change the data.
    """
    health = {
        'version': _read_repo_version(),
        'status': status,
        'last_update': _iso_utc(last_update),
        'extra': {},
    }
    _write_json_atomic(HEALTH_FILE, health)


def _merge_into_cache(trip_data, previous):
    """--broker/--limit scrape only some houses. Put those into the previous
    cache's trips (matched by trip and house name) instead of replacing the
    whole cache with the subset; houses not scraped this run keep their data."""
    if not previous:
        return trip_data
    fresh = {(t['name'], h['name']): h for t in trip_data for h in t['houses']}
    merged = []
    for trip in previous.get('trips', []):
        houses = [fresh.pop((trip['name'], h['name']), h) for h in trip['houses']]
        merged.append(dict(trip, houses=houses))
    for trip in trip_data:  # houses new since the last full scrape
        extra = [h for h in trip['houses'] if (trip['name'], h['name']) in fresh]
        if not extra:
            continue
        target = next((t for t in merged if t['name'] == trip['name']), None)
        if target is None:
            merged.append(dict(trip, houses=extra))
        else:
            target['houses'].extend(extra)
    return merged


def _store_scrape_result(trip_data, scrape_stats):
    """Scrape step's last act. A run that passes _validate_scrape_output()
    replaces cache/houses.json and returns it. A failed run keeps the last
    good data and only flags it status=down (old updated_at kept), so the
    next render publishes the failure instead of hiding it; returns None."""
    if not _validate_scrape_output(trip_data, scrape_stats):
        previous = _load_houses_cache()
        if previous:
            previous['status'] = 'down'
            _write_json_atomic(HOUSES_CACHE_FILE, previous)
        _write_health('down', previous['updated_at'] if previous else None)
        return None
    cached = {
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'status': 'degraded' if scrape_stats and scrape_stats['failed'] else 'ok',
        'trips': trip_data,
    }
    _write_json_atomic(HOUSES_CACHE_FILE, cached)
    if os.path.exists(HOUSES_PARTIAL_FILE):
        os.remove(HOUSES_PARTIAL_FILE)
    return cached


def _render_site(data, cached, version):
    """Render step: write public/index.html and public/health.json from the
    houses cache. Never scrapes."""
    os.makedirs('public', exist_ok=True)
    with app.app_context():
        html_content = _render_html(
            title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'),
            trips=cached['trips'], updated_at=cached['updated_at'], version=version,
        )
    with open('public/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    _write_health(cached.get('status', 'ok'), cached['updated_at'])
    # Older builds wrote the raw scrape here; the page never reads it, and
    # anything left in public/ gets published.
    if os.path.exists('public/data.json'):
        os.remove('public/data.json')


def _validate_scrape_output(trip_data, scrape_stats=None):
    """Return True if the run produced usable house data.

    Rejects two failure shapes: every trip has zero houses (e.g. an empty
    input.json), or every house that needed a live scrape came back as a
    cache-less failure placeholder (dead selectors, a bot-detection block
    hitting every request) — `houses` is never actually empty in that case
    since _scrape_one_house still appends an all-N/A placeholder per house,
    so `scrape_stats` (attempted vs. failed scrape attempts) is what actually
    detects it. Publishing either would silently overwrite a working site.
    See WEBAPP_PROJECT_STANDARD.md §15B.
    """
    if not any(trip.get('houses') for trip in trip_data):
        return False
    if scrape_stats and scrape_stats['attempted'] and scrape_stats['failed'] == scrape_stats['attempted']:
        return False
    return True


def build_trip_data(data, driver=None, force_refresh=False, broker_filter=None, limit=None,  # pylint: disable=too-many-positional-arguments
                    on_house_scraped=None, scrape_stats=None):
    trips = []
    scraped = 0
    total = sum(
        1 for trip in data['trips'] for house in trip['houses']
        if not broker_filter or BROKER_DOMAINS.get(broker_filter) in house.get('house_url', '')
    )
    if limit is not None:
        total = min(total, limit)
    print(f"[progress] 0/{total} houses (0%)", flush=True)
    for trip in data['trips']:
        trip_checkin = trip.get('checkin', '')
        trip_checkout = trip.get('checkout', '')
        houses = []
        for house in trip['houses']:
            if limit is not None and scraped >= limit:
                break
            raw_url = house.get('house_url', '')
            house_url = (
                inject_dates(raw_url, trip_checkin, trip_checkout)
                if raw_url and trip_checkin and trip_checkout
                else raw_url
            )
            is_skipped = broker_filter and BROKER_DOMAINS.get(broker_filter) not in house_url
            if is_skipped:
                print(f"Skipping house: {house['name']} (not a {broker_filter} URL)")
                continue
            houses.append(_scrape_one_house(
                house, trip_checkin, trip_checkout, driver=driver, force_refresh=force_refresh,
                stats=scrape_stats,
            ))
            scraped += 1
            # Machine-readable progress for the /scrape skill: "[progress] 5/18 houses (27%)"
            print(f"[progress] {scraped}/{total} houses ({scraped * 100 // max(total, 1)}%)", flush=True)
            if on_house_scraped:
                current_trip = {
                    'name': trip.get('name', ''), 'checkin': trip_checkin,
                    'checkout': trip_checkout, 'houses': houses,
                }
                on_house_scraped(trips + [current_trip])
            # Add a polite cooldown between fewo-direkt houses to avoid
            # triggering rate-limits (DataDome tracks request cadence per IP).
            if scraped < (limit or 999) and 'fewo-direkt.de' in house_url:
                lo, hi = _config['fewo_cooldown_s']
                delay = random.uniform(lo, hi)
                print(f"  [fewo] cooling down {delay:.0f}s before next house …")
                time.sleep(delay)
        trips.append({
            'name': trip.get('name', ''), 'checkin': trip_checkin,
            'checkout': trip_checkout, 'houses': houses,
        })
    return trips


@app.route('/')
def index():
    with open('input.json', encoding='utf-8') as f:
        data = _normalize_input(json.load(f))
    return _render_html(
        title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'),
        trips=build_trip_data(data),
        updated_at=datetime.now().strftime('%Y-%m-%d %H:%M'),
        version=get_version(),
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Force re-fetch all sled runs, ignoring cache')
    parser.add_argument(
        '--broker', choices=['fewo', 'booking', 'huetten', 'interhome'],
        help='Only scrape houses from this broker',
    )
    parser.add_argument('--limit', type=int, help='Maximum number of houses to scrape')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        '--scrape-only', action='store_true',
        help='Scrape and update cache/houses.json, but do not render the site',
    )
    mode.add_argument(
        '--from-cache', action='store_true',
        help='Render the site from cache/houses.json without scraping (what deploys run)',
    )
    parser.add_argument(
        '--house', type=str, metavar='NAME',
        help='Scrape only this house (substring match), patch cache/houses.json and re-render',
    )
    parser.add_argument(
        '--lang', default='bar-DE',
        choices=['de-DE', 'en-GB', 'fr-FR', 'nl-NL', 'bar-DE', 'bar-AT', 'gsw-CH', 'nds-DE', 'pfl-DE'],
        help='Language for the rendered page (default: bar-DE)',
    )
    args = parser.parse_args()
    if args.house and args.from_cache:
        parser.error('--house scrapes; it cannot be combined with --from-cache')

    _translations = load_translations(args.lang)
    _all_translations = load_all_translations()
    _lang = args.lang

    start_time = time.time()
    version = get_version()

    with open('input.json', encoding='utf-8') as f:
        data = _normalize_input(json.load(f))

    if args.from_cache:
        cached = _load_houses_cache()
        if not cached:
            _write_health('down', None)
            print(
                "ERROR: no cache/houses.json yet -- run `python app.py --scrape-only` "
                "(or plain `python app.py`) once first."
            )
            raise SystemExit(1)
        _render_site(data, cached, version)
        print(f"HTML rendered from cache/houses.json (scraped {cached['updated_at']}, "
              f"status {cached.get('status', 'ok')}) in {time.time() - start_time:.1f}s")
        raise SystemExit(0)

    rodelwelten.load_cache(CACHE_FILE)
    outdooractive.load_cache(CACHE_FILE_OA)

    driver = None
    try:
        driver = _make_driver()
        print("Using Selenium (headless Chrome) for JS-rendered pages")
    except Exception as e:
        print(f"Selenium unavailable ({e}), falling back to requests")

    if args.house:
        needle = args.house.lower()
        # Find all matching houses across trips
        matches = [
            (trip, house)
            for trip in data['trips']
            for house in trip['houses']
            if needle in house['name'].lower()
        ]
        if not matches:
            print(f"No house found matching '{args.house}'. Available houses:")
            for trip in data['trips']:
                for house in trip['houses']:
                    print(f"  [{trip.get('name', '')}] {house['name']}")
            raise SystemExit(1)
        # Allow multiple matches only when all share the exact same name (same house, multiple trips)
        unique_names = {h['name'] for _, h in matches}
        if len(unique_names) > 1:
            print(f"Multiple different houses match '{args.house}':")
            for trip, house in matches:
                print(f"  [{trip.get('name', '')}] {house['name']}")
            print("Use a more specific name.")
            raise SystemExit(1)
        cached = _load_houses_cache()
        if not cached:
            print("ERROR: no cache/houses.json to patch -- run a full `python app.py --scrape-only` first.")
            raise SystemExit(1)

        print(f"Matched {len(matches)} entr{'y' if len(matches)==1 else 'ies'}: {matches[0][1]['name']}")
        for trip, house in matches:
            print(f"  [{trip.get('name', '')}]")

        # Group matches by unique (checkin, checkout) — prices differ per date range
        date_groups = {}
        for trip, house in matches:
            key = (trip.get('checkin', ''), trip.get('checkout', ''))
            date_groups.setdefault(key, (trip, house))

        try:
            scraped = {}  # key -> fresh house data
            for key, (trip, house) in date_groups.items():
                checkin, checkout = key
                print(f"Scraping for dates {checkin} → {checkout}")
                scraped[key] = _scrape_one_house(house, checkin, checkout, driver=driver, force_refresh=args.force)
        finally:
            if driver:
                driver.quit()

        rodelwelten.save_cache()
        outdooractive.save_cache()

        replaced = 0
        house_name = matches[0][1]['name']
        for t in cached['trips']:
            key = (t.get('checkin', ''), t.get('checkout', ''))
            if key not in scraped:
                continue
            for i, h in enumerate(t['houses']):
                if h['name'] == house_name:
                    t['houses'][i] = scraped[key]
                    replaced += 1
        if replaced == 0:
            print(f"House '{house_name}' not found in cache/houses.json — appending to first matching trip.")
            first_trip, first_house = matches[0]
            key = (first_trip.get('checkin', ''), first_trip.get('checkout', ''))
            for t in cached['trips']:
                if t['name'] == first_trip.get('name', ''):
                    t['houses'].append(scraped[key])
                    replaced += 1
                    break
        if replaced == 0:
            print("Warning: could not find matching trip in cache/houses.json either.")
        else:
            print(f"Patched {replaced} entr{'y' if replaced==1 else 'ies'} in cache/houses.json")

        cached['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        _write_json_atomic(HOUSES_CACHE_FILE, cached)
        if not args.scrape_only:
            _render_site(data, cached, version)
        print(f"Done in {time.time() - start_time:.1f}s")
        raise SystemExit(0)

    def _save_partial(partial_trips):
        rodelwelten.save_cache()
        outdooractive.save_cache()
        _write_json_atomic(HOUSES_PARTIAL_FILE, {
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'), 'trips': partial_trips,
        })
        print("  [cache] cache/houses.partial.json updated")

    scrape_stats = {'attempted': 0, 'failed': 0}
    try:
        trip_data = build_trip_data(
            data, driver=driver, force_refresh=args.force,
            broker_filter=args.broker, limit=args.limit,
            on_house_scraped=_save_partial, scrape_stats=scrape_stats,
        )
    finally:
        if driver:
            driver.quit()

    rodelwelten.save_cache()
    outdooractive.save_cache()

    if args.broker or args.limit is not None:
        trip_data = _merge_into_cache(trip_data, _load_houses_cache())
    cached = _store_scrape_result(trip_data, scrape_stats)
    if cached is None:
        print(
            "ERROR: scrape yielded zero houses across all trips — aborting without "
            "replacing cache/houses.json or public/index.html. The previous data "
            "stays in place."
        )
        raise SystemExit(1)
    print(f"Data saved to cache/houses.json (status {cached['status']})")

    if not args.scrape_only:
        _render_site(data, cached, version)
        print("Static site generated in public/index.html")
    print(f"Done in {time.time() - start_time:.1f}s")
